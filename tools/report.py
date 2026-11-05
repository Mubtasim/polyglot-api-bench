#!/usr/bin/env python3
"""Turns a run's raw k6 results and docker stats samples into a report.

    python3 tools/report.py rates results/<run>   # prints "<endpoint> <rate>" for the latency phase
    python3 tools/report.py build results/<run>   # writes summary.csv, RESULTS.md, charts/*.svg
"""
import csv
import json
import math
import pathlib
import re
import statistics
import sys

APIS = ["go", "rust", "dotnet", "node"]
ENDPOINTS = ["health", "hash", "json", "primes", "users", "fanout"]
LABELS = {"go": "Go (Gin)", "rust": "Rust (Axum)", "dotnet": ".NET (ASP.NET Core)", "node": "Node.js (NestJS)"}
SHORT = {"go": "Go", "rust": "Rust", "dotnet": ".NET", "node": "Node.js"}
ENDPOINT_INFO = {
    "health": ("GET /health", "framework overhead"),
    "hash": ("POST /hash", "bcrypt, cost 10: CPU in a library"),
    "json": ("POST /json/transform", "parse and aggregate 574 KB of JSON"),
    "primes": ("GET /compute/primes?n=1000000", "sieve: tight loops over memory"),
    "users": ("GET /users?limit=50", "Postgres query + row mapping"),
    "fanout": ("GET /fanout", "10 parallel 50 ms waits per request"),
}
LATENCY_FRACTION = 0.5
K6_BOUND_SHARE = 0.9
DROPPED_SHARE = 0.01


def latency_compromised(row):
    """True when the fixed-rate test lost requests: errors, or > DROPPED_SHARE never sent."""
    if row.get("lat_p99_ms") is None:
        return False
    scheduled = (row.get("lat_requests") or 0) + (row.get("dropped") or 0)
    return (row.get("lat_error_pct") or 0) > 0 or (scheduled and (row.get("dropped") or 0) / scheduled > DROPPED_SHARE)

NAME_RE = re.compile(rf"^(?P<api>[a-z]+)-(?P<ep>{'|'.join(ENDPOINTS)})-(?P<phase>throughput|latency)(?:-(?P<rep>\d+))?$")
MEM_RE = re.compile(r"^([\d.]+)\s*([KMG]i?B|B)$")
MEM_FACTOR = {"B": 1 / 1048576, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024,
              "KB": 1e3 / 1048576, "MB": 1e6 / 1048576, "GB": 1e9 / 1048576}


# ---------- parsing ----------

def parse_mem(text):
    """Converts docker's memory strings ("12.5MiB") to MiB."""
    m = MEM_RE.match(text.strip())
    if not m:
        raise ValueError(f"unknown memory value {text!r}")
    return float(m.group(1)) * MEM_FACTOR[m.group(2)]


def parse_name(stem):
    """'go-hash-throughput-2' -> ('go', 'hash', 'throughput', 2); None for other files."""
    m = NAME_RE.match(stem)
    if not m:
        return None
    return m["api"], m["ep"], m["phase"], int(m["rep"]) if m["rep"] else None


def parse_stats(lines):
    """Splits 'unix_ms,container,cpu%,mem' samples into API and k6 lists of (cpu%, MiB)."""
    out = {"api": [], "k6": []}
    for line in lines:
        parts = line.strip().split(",")
        if len(parts) != 4:
            continue
        _, name, cpu, mem = parts
        try:
            sample = (float(cpu.rstrip("%")), parse_mem(mem))
        except ValueError:
            continue
        out["k6" if name == "pab-k6" else "api"].append(sample)
    return out


def load(run_dir):
    raw = pathlib.Path(run_dir) / "raw"
    throughput, latency, stats, startup = {}, {}, {}, {}
    for path in sorted(raw.glob("*.json")):
        if "-startup-" in path.stem:
            s = json.loads(path.read_text())
            startup.setdefault(s["api"], []).append(s)
            continue
        parsed = parse_name(path.stem)
        if not parsed:
            continue
        api, ep, phase, _ = parsed
        target = throughput if phase == "throughput" else latency
        target.setdefault((api, ep), []).append(json.loads(path.read_text()))
    for path in raw.glob("*.stats"):
        parsed = parse_name(path.stem)
        if parsed:
            api, ep, phase, _ = parsed
            stats[(api, ep, phase)] = parse_stats(path.read_text().splitlines())
    return throughput, latency, stats, startup


# ---------- latency-phase rates ----------

def target_rates(throughput):
    """Per endpoint: LATENCY_FRACTION of the lowest median throughput, at least 1 request/s."""
    medians = {}
    for (api, ep), runs in throughput.items():
        medians.setdefault(ep, []).append(statistics.median(r["rps"] for r in runs))
    return {ep: max(1, math.floor(min(values) * LATENCY_FRACTION)) for ep, values in medians.items()}


# ---------- aggregation ----------

def summarize(run_dir):
    host = json.loads((pathlib.Path(run_dir) / "host.json").read_text())
    throughput, latency, stats, startup = load(run_dir)
    k6_limit = host.get("k6Cpus", 1) * 100 * K6_BOUND_SHARE
    rows = []
    for ep in ENDPOINTS:
        for api in APIS:
            runs = throughput.get((api, ep))
            lat = (latency.get((api, ep)) or [None])[0]
            if not runs and not lat:
                continue
            row = {"endpoint": ep, "api": api}
            if runs:
                rps = [r["rps"] for r in runs]
                mid = sorted(runs, key=lambda r: r["rps"])[len(runs) // 2]
                row.update(rps_median=statistics.median(rps), rps_min=min(rps), rps_max=max(rps),
                           tp_p50_ms=mid["latencyMs"]["p50"], tp_p99_ms=mid["latencyMs"]["p99"],
                           tp_error_pct=100 * max(r["errorRate"] for r in runs))
            if lat:
                row.update(target_rate=lat.get("targetRate"), lat_rps=lat["rps"],
                           lat_p50_ms=lat["latencyMs"]["p50"], lat_p95_ms=lat["latencyMs"]["p95"],
                           lat_p99_ms=lat["latencyMs"]["p99"], lat_max_ms=lat["latencyMs"]["max"],
                           lat_error_pct=100 * lat["errorRate"], dropped=lat["droppedIterations"],
                           lat_requests=lat["requests"])
            samples = [s for phase in ("throughput", "latency") for s in stats.get((api, ep, phase), {}).get("api", [])]
            tp = stats.get((api, ep, "throughput"), {})
            if samples:
                row["peak_mem_mib"] = max(m for _, m in samples)
            if tp.get("api"):
                row["api_cpu_pct"] = statistics.mean(c for c, _ in tp["api"])
            if tp.get("k6"):
                row["k6_cpu_pct"] = statistics.mean(c for c, _ in tp["k6"])
                row["k6_bound"] = row["k6_cpu_pct"] > k6_limit
            rows.append(row)
    return host, rows, startup


CSV_FIELDS = ["endpoint", "api", "rps_median", "rps_min", "rps_max", "tp_p50_ms", "tp_p99_ms", "tp_error_pct",
              "target_rate", "lat_rps", "lat_p50_ms", "lat_p95_ms", "lat_p99_ms", "lat_max_ms", "lat_error_pct",
              "dropped", "peak_mem_mib", "api_cpu_pct", "k6_cpu_pct", "k6_bound"]


# ---------- formatting ----------

def fmt_rps(v):
    return "–" if v is None else f"{v:,.0f}" if v >= 100 else f"{v:.1f}"


def fmt_ms(v):
    return "–" if v is None else f"{v:.2f}" if v < 10 else f"{v:.1f}" if v < 100 else f"{v:,.0f}"


def fmt_tick(v):
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def fmt_num(v, digits=0):
    return "–" if v is None else f"{v:,.{digits}f}"


# ---------- charts ----------

# Categorical slots 1-4 of the dataviz reference palette, validated for light and
# dark surfaces. Colors follow the API, never its rank.
COLORS = {"go": ("#2a78d6", "#3987e5"), "rust": ("#eb6834", "#d95926"),
          "dotnet": ("#1baf7a", "#199e70"), "node": ("#eda100", "#c98500")}


def svg_style():
    light = "".join(f"--{a}:{c[0]};" for a, c in COLORS.items())
    dark = "".join(f"--{a}:{c[1]};" for a, c in COLORS.items())
    return (
        "<style>"
        f"svg{{--surface:#fcfcfb;--text:#0b0b0b;--text2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;{light}}}"
        "@media (prefers-color-scheme: dark){"
        f"svg{{--surface:#1a1a19;--text:#f0efec;--text2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;{dark}}}}}"
        "text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;fill:var(--text2);font-size:12px}"
        ".title{fill:var(--text);font-size:15px;font-weight:600}"
        ".panel{fill:var(--text);font-size:13px;font-weight:600}"
        ".note{fill:var(--muted);font-size:11px}"
        ".value{fill:var(--text);font-variant-numeric:tabular-nums}"
        "</style>"
    )


def bar_path(x, y, w, h, r=4):
    """A bar anchored at x with a rounded data end on the right."""
    if w <= 0:
        return ""
    r = min(r, w, h / 2)
    return (f'<path d="M{x:.1f},{y:.1f} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r:.1f} '
            f'a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z"/>')


def nice_max(v):
    if v <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(v))
    for step in (1, 2, 2.5, 5, 10):
        if step * exp >= v:
            return step * exp
    return 10 * exp


def panel(y, title, note, items, fmt, whiskers=None):
    """One horizontal bar panel. items: [(api, value)]. Returns (svg, height)."""
    label_w, x0, plot_w, bar_h, gap = 92, 100, 560, 18, 8
    vmax = nice_max(max([v for _, v in items] + [hi for _, hi in (whiskers or {}).values()] + [0]))
    out = [f'<text class="panel" x="0" y="{y + 14}">{title}</text>',
           f'<text class="note" x="0" y="{y + 30}">{note}</text>']
    top = y + 42
    rows_h = len(items) * (bar_h + gap)
    for i in range(5):
        gx = x0 + plot_w * i / 4
        out.append(f'<line x1="{gx:.1f}" y1="{top - 4}" x2="{gx:.1f}" y2="{top + rows_h - gap + 4}" '
                   f'stroke="var(--grid)" stroke-width="1"/>')
        out.append(f'<text class="note" x="{gx:.1f}" y="{top + rows_h + 10}" text-anchor="middle">'
                   f'{fmt_tick(vmax * i / 4)}</text>')
    for i, (api, v) in enumerate(items):
        by = top + i * (bar_h + gap)
        w = plot_w * v / vmax
        out.append(f'<text x="{label_w}" y="{by + 13}" text-anchor="end">{SHORT[api]}</text>')
        out.append(f'<g fill="var(--{api})">{bar_path(x0, by, w, bar_h)}</g>')
        end = w
        if whiskers and api in whiskers:
            lo, hi = whiskers[api]
            x_lo, x_hi = x0 + plot_w * lo / vmax, x0 + plot_w * hi / vmax
            cy = by + bar_h / 2
            out.append(f'<path d="M{x_lo:.1f},{cy - 4:.1f} v8 M{x_lo:.1f},{cy:.1f} H{x_hi:.1f} '
                       f'M{x_hi:.1f},{cy - 4:.1f} v8" stroke="var(--text)" stroke-width="1.5" fill="none"/>')
            end = max(end, x_hi - x0)
        out.append(f'<text class="value" x="{x0 + end + 6:.1f}" y="{by + 13}">{fmt(v)}</text>')
    out.append(f'<line x1="{x0}" y1="{top - 4}" x2="{x0}" y2="{top + rows_h - gap + 4}" '
               f'stroke="var(--axis)" stroke-width="1"/>')
    return "".join(out), 42 + rows_h + 22


def endpoint_chart(ep, rows, rate):
    path, what = ENDPOINT_INFO[ep]
    width = 760
    parts, y = [], 52
    tp = [(r["api"], r["rps_median"]) for r in rows if r.get("rps_median") is not None]
    if tp:
        whiskers = {r["api"]: (r["rps_min"], r["rps_max"]) for r in rows if r.get("rps_median") is not None}
        svg, h = panel(y, "Throughput (requests/s, higher is better)",
                       "median of the runs; the whisker spans the slowest and fastest run", tp, fmt_rps, whiskers)
        parts.append(svg)
        y += h + 16
    lat = [(r["api"], r["lat_p99_ms"]) for r in rows if r.get("lat_p99_ms") is not None]
    if lat:
        svg, h = panel(y, f"p99 latency at {fmt_num(rate)} requests/s (ms, lower is better)",
                       "the same fixed request rate for every API", lat, fmt_ms)
        parts.append(svg)
        y += h + 16
    mem = [(r["api"], r["peak_mem_mib"]) for r in rows if r.get("peak_mem_mib") is not None]
    if mem:
        svg, h = panel(y, "Peak memory (MiB, lower is better)", "highest docker stats sample during the tests",
                       mem, lambda v: fmt_num(v))
        parts.append(svg)
        y += h
    height = y + 12
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="-20 0 {width} {height}" role="img" aria-label="{path} results">'
            f'{svg_style()}<rect x="-20" y="0" width="{width}" height="{height}" rx="8" fill="var(--surface)"/>'
            f'<text class="title" x="0" y="24">{path}</text><text class="note" x="0" y="40">{what}</text>'
            + "".join(parts) + "</svg>")


# ---------- report ----------

def build(run_dir):
    run_dir = pathlib.Path(run_dir)
    host, rows, startup = summarize(run_dir)

    with open(run_dir / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})

    charts = run_dir / "charts"
    charts.mkdir(exist_ok=True)
    by_ep = {}
    for r in rows:
        by_ep.setdefault(r["endpoint"], []).append(r)
    for ep, ep_rows in by_ep.items():
        rate = next((r["target_rate"] for r in ep_rows if r.get("target_rate")), None)
        (charts / f"{ep}.svg").write_text(endpoint_chart(ep, ep_rows, rate))

    (run_dir / "RESULTS.md").write_text(markdown(host, rows, by_ep, startup))


def markdown(host, rows, by_ep, startup):
    d = host["durations"]
    apis = [a for a in APIS if any(r["api"] == a for r in rows)]
    lines = [
        f"# Results: {host['runId']}",
        "",
        "| Host | |",
        "|---|---|",
        f"| CPU | {host['cpu']} ({host['logicalCpus']} logical CPUs) |",
        f"| Memory | {host['memoryGiB']} GiB |",
        f"| Kernel, Docker | {host['kernel']}, Docker {host['docker']} |",
        f"| CPU pinning | API `{host['cpusets']['api']}`, Postgres `{host['cpusets']['postgres']}`, "
        f"k6 `{host['cpusets']['k6']}` (profile `{host['profile']}`) |",
        f"| Throughput phase | {d['repeats']} × {d['throughput']} per endpoint after a {d['warmup']} warm-up |",
        f"| Latency phase | {d['latency']} per endpoint at {int(LATENCY_FRACTION * 100)}% of the slowest API's "
        f"median throughput, after a {d['warmup']} warm-up |",
        f"| Date | {host['date']} |",
        "",
        "## Overview",
        "",
        "Median throughput in requests/s (higher is better):",
        "",
        "| Endpoint | " + " | ".join(SHORT[a] for a in apis) + " |",
        "|---|" + "---:|" * len(apis),
    ]
    for ep, ep_rows in by_ep.items():
        vals = {r["api"]: r.get("rps_median") for r in ep_rows}
        lines.append(f"| `{ep}` | " + " | ".join(fmt_rps(vals.get(a)) for a in apis) + " |")
    lines += ["", "p99 latency in ms at the same fixed rate for every API (lower is better):", "",
              "| Endpoint | Rate (req/s) | " + " | ".join(SHORT[a] for a in apis) + " |",
              "|---|---:|" + "---:|" * len(apis)]
    marked = False
    for ep, ep_rows in by_ep.items():
        cells = {}
        for r in ep_rows:
            cells[r["api"]] = fmt_ms(r.get("lat_p99_ms")) + (" ⚠️" if latency_compromised(r) else "")
            marked = marked or latency_compromised(r)
        rate = next((r.get("target_rate") for r in ep_rows if r.get("target_rate")), None)
        lines.append(f"| `{ep}` | {fmt_num(rate)} | " + " | ".join(cells.get(a, "–") for a in apis) + " |")
    if marked:
        lines += ["", f"⚠️ Requests failed, or more than {int(DROPPED_SHARE * 100)}% of the scheduled requests "
                  "could not be sent: the p99 only covers the requests that completed. See the endpoint's table."]

    for ep, ep_rows in by_ep.items():
        path, what = ENDPOINT_INFO[ep]
        lines += ["", f"## `{path}`", "", what[0].upper() + what[1:] + ".", "",
                  f"![{path} results](charts/{ep}.svg)", "",
                  "| API | Req/s median (min–max) | p50 / p99 ms at max load | Errors | "
                  "p50 / p95 / p99 / max ms at fixed rate | Dropped | Peak MiB | API CPU % | k6 CPU % |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in ep_rows:
            bound = " ⚠️ load-generator bound" if r.get("k6_bound") else ""
            errors = max(r.get("tp_error_pct") or 0, r.get("lat_error_pct") or 0)
            lines.append(
                f"| {LABELS[r['api']]} | {fmt_rps(r.get('rps_median'))} "
                f"({fmt_rps(r.get('rps_min'))}–{fmt_rps(r.get('rps_max'))}) | "
                f"{fmt_ms(r.get('tp_p50_ms'))} / {fmt_ms(r.get('tp_p99_ms'))} | {errors:.2f}% | "
                f"{fmt_ms(r.get('lat_p50_ms'))} / {fmt_ms(r.get('lat_p95_ms'))} / {fmt_ms(r.get('lat_p99_ms'))} / "
                f"{fmt_ms(r.get('lat_max_ms'))} | {fmt_num(r.get('dropped'))} | {fmt_num(r.get('peak_mem_mib'))} | "
                f"{fmt_num(r.get('api_cpu_pct'))} | {fmt_num(r.get('k6_cpu_pct'))}{bound} |")
        if any(r.get("k6_bound") for r in ep_rows):
            lines += ["", f"⚠️ k6 averaged over {int(K6_BOUND_SHARE * 100)}% of its {host.get('k6Cpus')} CPUs: "
                      "the load generator, not the API, may have set the ceiling for the marked rows."]

    lines += ["", "## Footprint", "", "| API | Image size (MB) | Startup to first 200 (ms) | Memory after start |",
              "|---|---:|---:|---:|"]
    for a in apis:
        s = startup.get(a, [])
        ms = statistics.median(x["startupMs"] for x in s) if s else None
        idle = s[0]["idleMem"] if s else "–"
        size = host.get("images", {}).get(a, {}).get("sizeMB")
        lines.append(f"| {LABELS[a]} | {fmt_num(size, 1)} | {fmt_num(ms)} | {idle} |")
    lines += ["", "CPU % is from `docker stats`: 100% = one logical CPU fully busy. "
              "Startup is measured from `docker compose up` to the first successful `/health`, "
              "so it includes container creation."]
    return "\n".join(lines) + "\n"


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("rates", "build"):
        sys.exit(__doc__)
    cmd, run_dir = sys.argv[1], sys.argv[2]
    if cmd == "rates":
        throughput, _, _, _ = load(run_dir)
        for ep, rate in target_rates(throughput).items():
            print(ep, rate)
    else:
        build(run_dir)


if __name__ == "__main__":
    main()
