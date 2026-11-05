# Design

The same small HTTP API is built four times: Go (Gin), Rust (Axum), .NET (ASP.NET Core Minimal APIs) and Node.js (NestJS on Fastify). A harness then measures each one under identical, reproducible conditions. The goal is to explain *where* the runtimes differ and why, not to crown a winner.

## 1. Goals and non-goals

**Goals**
- Four implementations of one contract (section 2), each written the way a typical team would write it with that stack: idiomatic code and default runtime settings, no exotic tuning.
- Proof that the four return the same answers (section 5) before any number is trusted.
- Two measurements per endpoint: **maximum throughput** at fixed concurrency, and **latency at a fixed request rate** that every implementation can sustain (section 4).
- Runs on a 2-core laptop with CPU pinning, and on a larger VM by changing one profile file.
- Results committed to the repo with the hardware, versions and image digests used.

**Non-goals**
- Tuned, "fastest possible" versions of each stack. TechEmpower already does that.
- Two-machine runs (load generator on a separate host). The VM profile pins the load generator to its own cores instead; a two-machine mode can come later.
- Node.js cluster mode, .NET Native AOT and other variants. They are listed as follow-ups in the README.

## 2. API contract

All four servers listen on port 8080, return `application/json`, and use camelCase field names. Errors are `400` with `{"error": "<message>"}`; the message text is free-form.

| Endpoint | Request | Response | What it stresses |
|---|---|---|---|
| `GET /health` | none | `{"status":"ok"}` | Framework overhead (control) |
| `POST /hash` | `{"password": "<1–72 bytes>"}` | `{"hash": "<bcrypt, cost 10>"}` | CPU-bound work in a library, use of all cores |
| `POST /json/transform` | `{"orders": [Order, ...]}` (about 600 KB) | Summary (below) | JSON parsing, allocation, garbage collection |
| `GET /compute/primes?n=` | `n` integer, 0–10,000,000, default 1,000,000 | `{"n", "count", "largest"}` | Tight loops over memory |
| `GET /users?limit=` | `limit` integer, 1–1000, default 50 | `[{"id", "name", "email", "createdAt"}]` ordered by `id` | Database driver, pool, row mapping |
| `GET /fanout` | none | `{"calls": 10, "results": [{"service": "service-<i>", "status": "ok"}, ...]}` in index order | Many concurrent waits (10 timers of 50 ms per request) |

**Validation.** A missing or empty password, a password over 72 bytes, a non-integer or out-of-range `n` or `limit` all return 400. Missing `n`/`limit` use the default.

**`/json/transform`.** `Order = {id: int, customer: string, category: string, amount: number, quantity: int, tags: [string]}`. Keep orders with `amount > 50`. For each kept order, `revenue = amount × quantity`. Response:
- `totalOrders`: number of orders received; `matchedOrders`: number kept.
- `categories`: one entry per category among kept orders, `{category, count, revenue, avgAmount}` where `revenue` is the sum of revenue and `avgAmount` the mean `amount`; sorted by `revenue` descending, then `category` ascending.
- `topOrders`: the 10 kept orders with the highest revenue, `{id, customer, revenue}`, sorted by `revenue` descending, then `id` ascending.
- `tagCounts`: object mapping each tag to how many times it appears across kept orders (a tag repeated within one order counts each time).

**`/compute/primes`.** Sieve of Eratosthenes up to and including `n`. `count` is the number of primes ≤ n, `largest` the largest prime ≤ n (0 when n < 2).

**`/users`.** `createdAt` is an ISO 8601 timestamp in UTC. Each stack uses its native timestamp type and serializer, so the exact formatting may differ (`Z` vs `+00:00`, fractional digits); the parity check compares instants, not strings.

**`/fanout`.** Simulates calling 10 downstream services in parallel. Each "call" is a 50 ms non-blocking timer, so a request takes about 50 ms, not 500 ms. Timers instead of real HTTP calls keep a downstream service from sharing CPU with the API under test.

## 3. Implementations

| | Go | Rust | .NET | Node.js |
|---|---|---|---|---|
| Runtime | Go 1.27.3 | Rust 1.99.1 | .NET 10.0 | Node.js 26 LTS |
| Framework | Gin 1.12 | Axum 0.8 + Tokio | ASP.NET Core Minimal APIs | NestJS 12 + Fastify adapter |
| Postgres driver | pgx 5 (pgxpool) | SQLx 0.9 | Npgsql 10 | pg 8 |
| bcrypt | `golang.org/x/crypto/bcrypt` (pure Go) | `bcrypt` crate (pure Rust) | BCrypt.Net-Next (managed) | `bcrypt` (native C++, runs on libuv's thread pool) |
| Runtime image | distroless static | distroless cc | ASP.NET chiseled | node slim |

Shared rules:
- Release builds, request logging off, database pool of 20 connections, request body limit 10 MB.
- Default runtime settings. Go reads its CPU limit from the container; .NET uses its default server GC; Node runs one process with libuv's default 4 threads; Tokio sizes its worker pool from the CPUs it can use.
- Rust runs bcrypt on Tokio's blocking pool (`spawn_blocking`), the documented way to do slow CPU work in an async handler. The sieve and the JSON transform run inline, as a typical handler would.
- All dependencies pinned: `go.sum`, `Cargo.lock`, `package-lock.json`, `packages.lock.json`. Base images pinned to exact versions.
- Each API has its own small README covering the idiomatic choices it makes.

## 4. Benchmark harness

**Topology.** Docker Compose runs Postgres 18, one API at a time, and k6 as the load generator. Containers are pinned to CPUs by a **profile** (`bench/profiles/*.env`):

| Profile | API under test | Postgres + k6 | API memory |
|---|---|---|---|
| `laptop` (2 cores, 4 threads) | CPUs `0,2` (one physical core) | CPUs `1,3` | 1 GB |
| `vm` (8+ vCPUs) | CPUs `0-1` | Postgres `2-3`, k6 `4-7` | 1 GB |

Pinning with `cpuset` (not a CPU quota) gives the API whole cores and keeps the load generator from stealing them. On the laptop, Postgres and k6 share a core, so `/users` and very fast endpoints can be limited by k6 or Postgres; the report flags this (below).

**Data.** Postgres is seeded with 10,000 users with fixed timestamps (`2026-01-01T00:00:00Z` plus `g` minutes). The 5,000-order payload is generated by `tools/gen_payload.py` with a fixed seed, so every run sends identical bytes.

**Run order** (`bench/run.sh`):
1. Build or pull images; record host info (CPU model, cores, RAM, kernel, Docker version) and image digests and sizes.
2. **Throughput phase.** For each API: start it, record startup time (container start → first `200` on `/health`) and idle memory, then for each endpoint run a 10 s warm-up (discarded) and **3 × 20 s** runs at fixed concurrency (closed model: N virtual users, each sending the next request when the previous answer arrives). Stop the API.
3. Pick each endpoint's **target rate** = 50% of the lowest median throughput among the APIs in this run, so every implementation can sustain it.
4. **Latency phase.** For each API: start it, warm up, and run each endpoint for 30 s at the target rate (open model: k6's `constant-arrival-rate`, requests start on schedule even if earlier ones are slow, so queueing shows up in the percentiles instead of being hidden). Stop the API.
5. Write the report.

Concurrency per endpoint: health 64, hash 32, json 16, primes 32, users 64, fanout 256.

**Measured per test:** requests/s; p50/p95/p99/max latency; error rate; dropped iterations (latency phase); API peak memory and average CPU from `docker stats` sampled every second; k6's own CPU use. If k6 averages over 90% of its cores, the row is marked **load-generator bound**.

**Duration.** The full laptop run takes about 45 minutes. `QUICK=1` runs one repetition of 5 s per test for checking the harness. `APIS=` and `ENDPOINTS=` select subsets.

## 5. Correctness: the parity check

`tools/parity.py` (Python 3 standard library only) runs against all four live APIs and fails on any mismatch:
- Computes the expected answer for `/json/transform` and `/compute/primes` itself (a reference implementation), and checks every API against it: floats compared within 1e-9 relative error, object key order ignored.
- Checks `/users` row by row against the seed formula, comparing timestamps as instants.
- Checks `/hash` returns a 60-character `$2a$`/`$2b$`/`$2y$` hash with cost 10, and that two hashes of the same password differ (random salt).
- Checks `/fanout` shape, order, and that it completes in under 200 ms (proving the 10 waits run in parallel).
- Checks the 400 cases from section 2 and the defaults.

CI runs it on every pull request; `bench/run.sh` runs it before measuring and stops if it fails.

## 6. Results and report

Each run writes `results/<date>-<profile>/`:
- `raw/`: k6 summary JSON per test, `docker stats` samples.
- `summary.csv`: one row per API × endpoint × phase.
- `RESULTS.md`: host and versions, tables per endpoint (throughput median with min–max of the 3 runs, latency percentiles at the target rate, memory, CPU), startup time, idle memory, image size.
- `charts/*.svg`: bar charts per endpoint, generated by `tools/report.py` (no dependencies), readable in light and dark GitHub themes.

Published runs are committed; the README links the latest and summarizes what the numbers show and why.

## 7. CI

GitHub Actions on every pull request and push to `main`:
- **lint**: gofmt + go vet; cargo fmt + clippy (warnings are errors); `dotnet build -warnaserror` + `dotnet format --verify-no-changes`; `tsc --noEmit`.
- **build**: the four images in a matrix with layer caching.
- **parity**: Compose brings up Postgres and all four APIs, then `tools/parity.py`.
- **smoke**: `QUICK=1` benchmark of every endpoint on one API, to keep the harness working.
- **publish** (on `main` only): push images to `ghcr.io/mubtasim/polyglot-api-bench-<api>` so a laptop or VM can pull instead of building.

## 8. Repository layout

```
apis/{go,rust,dotnet,node}/   one implementation each, with Dockerfile and README
db/init.sql                   schema + seed
bench/run.sh                  orchestrator
bench/k6/bench.js             k6 script (both phases)
bench/profiles/{laptop,vm}.env
tools/gen_payload.py          deterministic /json/transform payload
tools/parity.py               correctness check
tools/report.py               summary.csv, RESULTS.md, SVG charts
results/<run>/                committed runs
compose.yaml
.github/workflows/ci.yml
```

## 9. Risks

- **The laptop is small.** One physical core per API means multi-core advantages (Go, Rust and .NET using every core vs one Node.js thread) show up less than on the VM; the VM run is the fairer comparison and both are labeled.
- **Disk.** Building all four images needs about 5 GB; images are built one at a time, and published images let later runs skip building.
- **Microbenchmarks are not applications.** The README says so and keeps conclusions to what each endpoint measures.
