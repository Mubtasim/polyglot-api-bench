# polyglot-api-bench

[![CI](https://github.com/Mubtasim/polyglot-api-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/Mubtasim/polyglot-api-bench/actions/workflows/ci.yml)

The same small HTTP API built four times, in **Go (Gin)**, **Rust (Axum)**, **.NET (ASP.NET Core Minimal APIs)** and **Node.js (NestJS on Fastify)**, then measured under identical, reproducible conditions. The goal is to explain *where* the runtimes differ and *why*, not to crown a winner.

Code written with AI tools under my design and review. The design is in [docs/design.md](docs/design.md); the background on CPU, threads and memory that the analysis relies on is in [docs/cpu-threads-memory.md](docs/cpu-threads-memory.md).

## What is measured

| Endpoint | Work | What it stresses |
|---|---|---|
| `GET /health` | returns `{"status":"ok"}` | framework overhead |
| `POST /hash` | bcrypt, cost 10 | CPU-heavy work inside a library |
| `POST /json/transform` | parse 5,000 orders (574 KB), filter, group, rank | JSON parsing, allocation, garbage collection |
| `GET /compute/primes?n=1000000` | sieve of Eratosthenes | tight loops over memory |
| `GET /users?limit=50` | read 50 rows from Postgres | database driver, pool, serialization |
| `GET /fanout` | wait on 10 parallel 50 ms timers | many concurrent waits |

Every implementation follows one [contract](docs/design.md#2-api-contract) (same logic, same JSON, same validation), and [`tools/parity.py`](tools/parity.py) checks all four against a reference implementation before anything is measured, in CI and at the start of every run.

**How the comparison stays fair**
- **One API at a time**, pinned to one physical core with `cpuset`; Postgres and the load generator ([k6](https://k6.io)) are pinned to the other core, so they never share the API's CPU.
- **Two phases per endpoint.** *Throughput*: 3 × 20 s at fixed concurrency (closed model), after a 10 s warm-up so the .NET and V8 JIT compilers reach full speed. *Latency*: 30 s at a fixed request rate that every API can sustain (50% of the slowest API's throughput), with an open model so queueing shows up in the percentiles instead of slowing the load down.
- **Idiomatic code and default runtime settings**: release builds, no request logging, a pool of 20 database connections, no tuning beyond what a typical team would do.
- **Pinned versions**: Go 1.27.3, Rust 1.99.1, .NET 10.0.14, Node.js 26.13.0 LTS, Postgres 18.6, k6 2.3.0; lockfiles for every dependency.

## Results: laptop run, 2026-11-06

Intel i5-7200U (2 cores, 4 threads), 8 GB RAM. Each API gets one physical core (2 hardware threads). Full tables, raw k6 output and every chart: [results/2026-11-06-laptop](results/2026-11-06-laptop/RESULTS.md).

**Throughput** (median requests/s of 3 runs, higher is better). Most endpoints' runs agree within 2–5%; Node.js varied by 10–15% on `json`, `users` and `fanout`, where its one thread also runs the garbage collector's pauses:

| Endpoint | Go | Rust | .NET | Node.js |
|---|---:|---:|---:|---:|
| `health` ⁽¹⁾ | 13,049 | 13,347 | 11,959 | 12,819 |
| `hash` | 26.2 | 26.1 | 14.8 | **27.9** |
| `json` | 77.9 | **153** | 115 | 119 |
| `primes` | **308** | 237 | 298 | 218 |
| `users` ⁽²⁾ | **4,430** | 3,867 | 3,397 | 2,538 |
| `fanout` | 4,167 | 4,091 | **4,637** | 4,001 |

**CPU time per request** (ms; average API CPU ÷ requests/s, lower is better). This shows efficiency even where the API was not the bottleneck:

| Endpoint | Go | Rust | .NET | Node.js |
|---|---:|---:|---:|---:|
| `health` | 0.074 | **0.034** | 0.138 | 0.053 |
| `hash` | 74 | 77 | 134 | **72** |
| `json` | 25 | 13 | 17 | **10** ⁽³⁾ |
| `primes` | 6.4 | 8.4 | 6.5 | **5.7** ⁽³⁾ |
| `users` | **0.28** | 0.29 | 0.53 | 0.40 |
| `fanout` | 0.24 | 0.20 | 0.28 | **0.19** |

**p99 latency at a fixed rate** (ms, lower is better):

| Endpoint (rate) | Go | Rust | .NET | Node.js |
|---|---:|---:|---:|---:|
| `hash` (7/s) | 106 | 110 | 121 | **102** |
| `json` (38/s) | 49.5 | 40.6 | 45.7 | **33.4** |
| `primes` (108/s) | 25.2 | 26.4 | 24.6 | **21.4** |
| `users` (1,268/s) | **3.2** | **3.2** | 10.2 | 4.5 |
| `fanout` (2,000/s) | 99.8 | 76.4 | **67.1** | 110 |

**Footprint**

| | Go | Rust | .NET | Node.js |
|---|---:|---:|---:|---:|
| Image size | 41 MB | 47 MB | 184 MB | 395 MB |
| Startup to first response ⁽⁴⁾ | 1.0 s | 1.1 s | 1.5 s | 1.9 s |
| Peak memory, `/json` | 55 MiB | 37 MiB | 106 MiB | 159 MiB |

⁽¹⁾ On the laptop, k6 used its whole core before any API did: `/health` throughput measures the load generator, not the APIs; CPU per request is the meaningful number. ⁽²⁾ Go and Rust were not CPU-bound here (125% and 112% of 200%); the core shared by Postgres and k6 set their ceiling. ⁽³⁾ Node.js runs this on one thread, which gets a physical core to itself; the other three run two hardware threads on the same core, each slower than a thread alone (see [hyper-threading](docs/cpu-threads-memory.md#1-cores-hardware-threads-and-logical-cpus)), so per-thread CPU time flatters Node.js here. ⁽⁴⁾ Includes creating the container.

<p>
<img src="results/2026-11-06-laptop/charts/json.svg" alt="POST /json/transform: throughput, p99 latency at a fixed rate, and peak memory for the four APIs" width="760">
</p>

More charts: [health](results/2026-11-06-laptop/charts/health.svg) · [hash](results/2026-11-06-laptop/charts/hash.svg) · [primes](results/2026-11-06-laptop/charts/primes.svg) · [users](results/2026-11-06-laptop/charts/users.svg) · [fanout](results/2026-11-06-laptop/charts/fanout.svg)

## What the numbers show

**1. Libraries matter more than languages.** On `/hash` almost all the time is spent inside bcrypt, so the result is decided by the bcrypt *library*. Node.js wins narrowly (6%) with the native C++ `bcrypt` package, running on libuv's thread pool, which uses both hardware threads even though JavaScript is single-threaded. Go and Rust are close behind with well-optimized pure implementations. .NET needs 134 ms of CPU per hash against ~75 ms for the others, because BCrypt.Net-Next is plain managed code. For a login-heavy service, that is a 1.8× difference in servers.

**2. JSON parsing is where Rust's compile-time approach pays off.** On `/json/transform` Rust is 2× faster than Go. Rust's `serde` generates a parser for the exact request type at compile time and the handler borrows strings from the request instead of copying them; Go's standard `encoding/json` discovers the struct by reflection on every request and copies every string, then the garbage collector cleans up. Node.js's `JSON.parse` is optimized C++ inside V8: it reached 119 requests/s on a single thread, ahead of Go on two.

**3. Idiomatic code can be the bottleneck, not the language.** Rust was the slowest of the compiled languages on `/compute/primes`. A single-thread micro-benchmark on the same machine explains it: the inner loop `for j in (i*i..=n).step_by(i)` takes 3.90 ms per sieve, a plain `while` loop 2.53 ms, and Go's plain loop 2.64 ms. Rust's *inclusive* range iterator (`..=`) has to handle the edge case of reaching the maximum value, and that check stays inside the hot loop. The language is as fast as Go here; that one idiom is not. The implementation is left as measured.

**4. The single event loop shows up on CPU work.** Node.js is last on `/primes` and `/users`: its CPU sits at ~100% (one thread) while the others use ~200%. On this laptop the gap is small because the API has one physical core, and a second hardware thread adds only ~20–30%. On a machine with more cores Go, Rust and .NET would use all of them and the gap would grow several-fold; the usual Node.js answer is more processes (cluster mode or more containers).

**5. Waiting is cheap everywhere.** `/fanout` (10 × 50 ms timers per request) lands near the Little's-law ceiling of 256 clients ÷ 0.05 s = 5,120 requests/s for all four, using a fraction of the CPU. .NET's `Task.Delay` and `async`/`await` came closest (4,637/s) with the tightest tail (p99 67 ms at 2,000/s).

**6. At moderate load the gaps shrink.** At the fixed rates, where no API is overloaded, p99 latencies sit within ~1.5× of each other on every CPU endpoint, while maximum throughput differed by up to 2×. Throughput decides *how many servers you need*; at normal load users barely see the difference. One exception: .NET's `/users` p99 (10 ms against 3–4.5 ms) points to occasional pauses; garbage collection is the likely cause, not yet confirmed.

**7. Saturation is queueing.** Under maximum load latency is almost entirely waiting in line: `/hash` p50 was ~1.2 s with 32 clients (Little's law: 32 ÷ 26 req/s), while at 7 req/s the same request took 80–93 ms, which is just the hash itself.

**8. The bug the benchmark found.** During the fixed-rate `/health` test (5,979 requests/s, opening ~1,500 new connections in a burst), the Rust API lost 0.23% of requests with `dial: i/o timeout` and 2,216 were never sent. The kernel's counters for the container showed 3,339 dropped connection attempts (`ListenOverflows`) for Rust and none for Go. The cause was the **open-file limit**: containers start processes with a soft limit of 1,024 open files, and every connection uses one. Go, .NET and Node.js raise that limit to the hard limit (524,288) at startup; a Rust program does not. Past ~1,000 connections `accept()` failed with "too many open files", Axum paused accepting, the accept queue overflowed, and the kernel dropped new connections. [The fix](apis/rust/src/main.rs) raises the limit at startup (`rlimit::increase_nofile_limit`); the same burst afterwards: 5,953 requests/s, no errors, no dropped connections. The committed laptop results were measured before the fix, so Rust's `/health` fixed-rate row is marked in [RESULTS.md](results/2026-11-06-laptop/RESULTS.md). The same failure appears in production as a service that works until traffic passes about a thousand concurrent connections.

## Caveats

- **Small machine.** One physical core per API hides multi-core differences, and k6 sharing a core with Postgres caps the fastest endpoints. The [`vm` profile](bench/profiles/vm.env) (8+ vCPUs: API 2, Postgres 2, k6 4) is the fairer setup; a VM run is planned.
- **Microbenchmarks are not applications.** Each endpoint isolates one kind of work; a real service mixes them, plus logging, authentication and network calls.
- **One configuration each.** Defaults, one process, one framework per language. Other frameworks, tuning, Node.js cluster mode or .NET Native AOT would give different numbers.

## Run it yourself

Requirements: Docker with Compose, Python 3, and a machine with at least 4 logical CPUs.

```bash
bench/run.sh                    # full run, laptop profile (~55 minutes)
QUICK=1 bench/run.sh            # 5-second tests to check the harness (~5 minutes)
PROFILE=vm bench/run.sh         # 8+ vCPU profile
APIS="go rust" ENDPOINTS="json primes" bench/run.sh
PULL=1 bench/run.sh             # use the images CI publishes instead of building
```

Check the pinning in `bench/profiles/*.env` against `lscpu -e` first: the API's CPUs should be one whole physical core (both of its hardware threads), and nothing else should run on it. Results land in `results/<date>-<profile>/` with `RESULTS.md`, `summary.csv`, charts and raw k6 output.

To try the APIs by hand: `docker compose up -d`, then `curl localhost:8001/health` (Go), `:8002` (Rust), `:8003` (.NET), `:8004` (Node.js), and `python3 tools/parity.py --api go=http://localhost:8001`.

## Repository layout

```
apis/{go,rust,dotnet,node}/   one implementation each, with Dockerfile and README
bench/run.sh                  orchestrator (both phases, docker stats sampling, report)
bench/k6/bench.js             load script for both phases
bench/profiles/               CPU pinning per machine
tools/                        payload generator, parity check, report and chart generator (Python, no dependencies)
results/                      committed runs
docs/                         design and background notes
```

## Next

- A run on an 8-vCPU VM, where multi-core scaling shows.
- .NET 11 when it ships (mid-November 2026), and Native AOT as a variant.
- Node.js in cluster mode as a variant; Go's `encoding/json/v2` once it is the default.

## License

[MIT](LICENSE)
