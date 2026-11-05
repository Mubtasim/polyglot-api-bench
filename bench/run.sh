#!/usr/bin/env bash
# Runs the benchmark described in docs/design.md, section 4.
#
#   bench/run.sh                     full run with the laptop profile (~45 min)
#   QUICK=1 bench/run.sh             one short run per test, to check the harness
#   PROFILE=vm bench/run.sh          use bench/profiles/vm.env
#   APIS="go rust" ENDPOINTS="hash primes" bench/run.sh
#   PULL=1 bench/run.sh              pull published images instead of building
#   BUILD=0 bench/run.sh             use the images already on this machine
#
# Results go to results/<date>-<profile>/ (RESULTS.md, summary.csv, charts/).
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE="${PROFILE:-laptop}"
APIS=(${APIS:-go rust dotnet node})
ENDPOINTS=(${ENDPOINTS:-health hash json primes users fanout})
if [[ "${QUICK:-0}" == 1 ]]; then
  DURATION="${DURATION:-5s}" LATENCY_DURATION="${LATENCY_DURATION:-5s}" WARMUP="${WARMUP:-2s}" REPEATS="${REPEATS:-1}"
fi
DURATION="${DURATION:-20s}"
LATENCY_DURATION="${LATENCY_DURATION:-30s}"
WARMUP="${WARMUP:-10s}"
REPEATS="${REPEATS:-3}"

# shellcheck disable=SC1090
set -a; source "bench/profiles/$PROFILE.env"; set +a
SUFFIX=""
[[ "${QUICK:-0}" == 1 ]] && SUFFIX=-quick
RUN_ID="${RUN_ID:-$(date +%Y-%m-%d)-$PROFILE$SUFFIX}"
export RUN_DIR="$PWD/results/$RUN_ID" HOST_UID="$(id -u)" HOST_GID="$(id -g)"
export TAG="${TAG:-$([[ "${PULL:-0}" == 1 ]] && echo latest || echo local)}"

COMPOSE=(docker compose --progress quiet -f compose.yaml -f bench/compose.bench.yaml)

declare -A PORT=([go]=8001 [rust]=8002 [dotnet]=8003 [node]=8004)

log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }

check_cpus() {
  local max_cpu=$(($(nproc --all) - 1)) cpu
  for cpu in $(echo "$API_CPUSET,$DB_CPUSET,$K6_CPUSET" | tr ',-' '  '); do
    if ((cpu > max_cpu)); then
      echo "Profile $PROFILE uses CPU $cpu but this machine has CPUs 0-$max_cpu." >&2
      exit 1
    fi
  done
}

# Counts the CPUs in a cpuset like "0,2" or "4-7".
cpuset_size() {
  local n=0 part
  IFS=, read -ra parts <<< "$1"
  for part in "${parts[@]}"; do
    if [[ $part == *-* ]]; then n=$((n + ${part#*-} - ${part%-*} + 1)); else n=$((n + 1)); fi
  done
  echo "$n"
}

write_host_info() {
  local images=() api
  for api in "${APIS[@]}"; do images+=("ghcr.io/mubtasim/polyglot-api-bench-$api:$TAG"); done
  python3 - "$RUN_DIR/host.json" "${images[@]}" <<PY
import json, platform, subprocess, sys
def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
cpu = next((l.split(":", 1)[1].strip() for l in sh("lscpu").splitlines() if l.startswith("Model name")), "unknown")
mem_kb = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemTotal"))
images = {}
for ref in sys.argv[2:]:
    info = json.loads(sh("docker", "image", "inspect", ref) or "[{}]")[0]
    images[ref.split("polyglot-api-bench-")[1].split(":")[0]] = {
        "ref": ref, "id": info.get("Id"), "sizeMB": round(info.get("Size", 0) / 1e6, 1)}
json.dump({
    "runId": "$RUN_ID", "profile": "$PROFILE", "date": sh("date", "-Iseconds"),
    "cpu": cpu, "logicalCpus": int(sh("nproc", "--all")), "memoryGiB": round(mem_kb / 1048576, 1),
    "kernel": platform.release(), "docker": sh("docker", "version", "--format", "{{.Server.Version}}"),
    "cpusets": {"api": "$API_CPUSET", "postgres": "$DB_CPUSET", "k6": "$K6_CPUSET"},
    "k6Cpus": $(cpuset_size "$K6_CPUSET"),
    "durations": {"throughput": "$DURATION", "latency": "$LATENCY_DURATION", "warmup": "$WARMUP", "repeats": $REPEATS},
    "images": images,
}, open(sys.argv[1], "w"), indent=2)
PY
}

# Samples CPU and memory of the API and k6 containers about once a second
# until killed. Each line: unix_ms,name,cpu%,mem_used
sample_stats() {
  local out=$1; shift
  while true; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' "$@" 2> /dev/null \
      | sed "s/^/$(date +%s%3N),/; s| / [^,]*$||" >> "$out" || true
  done
}

run_k6() { # api endpoint phase duration out [extra env...]
  local api=$1 ep=$2 phase=$3 duration=$4 out=$5; shift 5
  "${COMPOSE[@]}" run --rm --no-deps --name pab-k6 \
    -e BASE_URL="http://$api:8080" -e ENDPOINT="$ep" -e PHASE="$phase" \
    -e DURATION="$duration" -e OUT="$out" "$@" \
    k6 run --quiet /scripts/bench.js
}

measured() { # api endpoint phase label [extra env...]
  local api=$1 ep=$2 phase=$3 label=$4; shift 4
  local stats="$RUN_DIR/raw/$api-$ep-$phase.stats" sampler
  sample_stats "$stats" "polyglot-api-bench-$api-1" pab-k6 &
  sampler=$!
  run_k6 "$api" "$ep" "$phase" "$([[ $phase == latency ]] && echo "$LATENCY_DURATION" || echo "$DURATION")" \
    "/results/raw/$api-$ep-$phase$label.json" "$@"
  kill "$sampler" 2> /dev/null || true
  wait "$sampler" 2> /dev/null || true
}

start_api() { # api phase -> waits for /health, records startup time and idle memory
  local api=$1 phase=$2 start end url="http://localhost:${PORT[$1]}/health"
  "${COMPOSE[@]}" rm -sf "$api" > /dev/null 2>&1 || true
  start=$(date +%s%3N)
  "${COMPOSE[@]}" up -d --no-build "$api" > /dev/null 2>&1
  until curl -sf -o /dev/null "$url"; do
    if (($(date +%s%3N) - start > 120000)); then echo "$api did not become healthy" >&2; exit 1; fi
    sleep 0.05
  done
  end=$(date +%s%3N)
  sleep 3
  local mem
  mem=$(docker stats --no-stream --format '{{.MemUsage}}' "polyglot-api-bench-$api-1" | sed 's| / .*||')
  printf '{"api": "%s", "phase": "%s", "startupMs": %d, "idleMem": "%s"}\n' \
    "$api" "$phase" $((end - start)) "$mem" > "$RUN_DIR/raw/$api-startup-$phase.json"
  log "$api up in $((end - start)) ms, idle memory $mem"
}

stop_api() { "${COMPOSE[@]}" stop "$1" > /dev/null 2>&1; }

# ---------------------------------------------------------------------------

check_cpus
mkdir -p "$RUN_DIR/raw" bench/data
rm -f "$RUN_DIR"/raw/*
python3 tools/gen_payload.py --out bench/data/orders.json > /dev/null

log "run $RUN_ID: profile $PROFILE (API on CPUs $API_CPUSET, Postgres $DB_CPUSET, k6 $K6_CPUSET)"
if [[ "${PULL:-0}" == 1 ]]; then
  "${COMPOSE[@]}" pull "${APIS[@]}"
elif [[ "${BUILD:-1}" == 1 ]]; then
  for api in "${APIS[@]}"; do "${COMPOSE[@]}" build "$api"; done # one at a time: builds are heavy
fi
"${COMPOSE[@]}" pull -q k6 postgres
"${COMPOSE[@]}" stop "${APIS[@]}" > /dev/null 2>&1 || true
"${COMPOSE[@]}" up -d --wait postgres > /dev/null
write_host_info

log "phase 1: throughput (${REPEATS} x $DURATION per endpoint after a $WARMUP warm-up)"
for api in "${APIS[@]}"; do
  start_api "$api" throughput
  python3 tools/parity.py --api "$api=http://localhost:${PORT[$api]}" || { log "parity check failed for $api"; exit 1; }
  for ep in "${ENDPOINTS[@]}"; do
    run_k6 "$api" "$ep" throughput "$WARMUP" "" > /dev/null
    for i in $(seq 1 "$REPEATS"); do
      printf '%s %-6s' "$(date +%H:%M:%S)" "$api"
      measured "$api" "$ep" throughput "-$i"
    done
    sleep 2
  done
  stop_api "$api"
done

log "phase 2: latency at a fixed rate (50% of the slowest API's throughput)"
declare -A RATE
while read -r ep rate; do RATE[$ep]=$rate; done < <(python3 tools/report.py rates "$RUN_DIR")
for api in "${APIS[@]}"; do
  start_api "$api" latency
  for ep in "${ENDPOINTS[@]}"; do
    run_k6 "$api" "$ep" latency "$WARMUP" "" -e RATE="${RATE[$ep]}" > /dev/null
    printf '%s %-6s' "$(date +%H:%M:%S)" "$api"
    measured "$api" "$ep" latency "" -e RATE="${RATE[$ep]}"
    sleep 2
  done
  stop_api "$api"
done

python3 tools/report.py build "$RUN_DIR"
log "done: $RUN_DIR/RESULTS.md"
log "Postgres is still running; stop everything with: docker compose down"
