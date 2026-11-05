# Production checklist

What to estimate, check and measure for any service, in any language or framework. Each item names the number to look for; where this benchmark ran into the problem, the item links to it. Background on the numbers: [cpu-threads-memory.md](cpu-threads-memory.md).

If time is short, do phase 1 and the trap list in phase 3: together they take under an hour and catch most problems.

## 1. Before building: estimate (15 minutes, written down)

1. **Peak load:** requests/s at the busiest hour, not the average. Peak is often 3–5× the average.
2. **CPU per request:** time one request of each main type with a profiler or a timer around the handler.
3. **Cores needed** = peak load × CPU per request. Example: 1,000 logins/s × 75 ms of bcrypt = 75 cores ([measured: 72–134 ms per hash](../results/2026-11-06-laptop/RESULTS.md)).
4. **Requests in flight** = load × latency (Little's law). This sizes thread pools, connection pools and memory.
5. **Memory** ≈ baseline + (memory per request × requests in flight) + garbage-collector headroom (often 2×). Parsed JSON is 5–10× its text size.
6. **Database connections** = pool size × instances. Keep it well below the database's `max_connections`.
7. **Headroom:** plan to run at 50–70% of the ceiling, so traffic spikes, deploys and the loss of one instance still fit.

If a number looks frightening (70 cores, 5,000 connections), change the design now, while it is cheap.

## 2. While building: code rules

1. **Never block where waiting is expected.** No CPU-heavy work on Node.js's event loop or on Tokio's worker threads; no synchronous I/O inside async code. Move heavy work to a worker pool or a separate process ([why](cpu-threads-memory.md#3-threads-and-how-each-runtime-runs-requests)).
2. **Put a limit on everything.** A timeout on every outbound call (database, HTTP, queue); a maximum request body size; a maximum page size; bounded queues and pools; retries with backoff, jitter and a cap. Anything unbounded will eventually take the server down.
3. **Release on every path.** Connections, locks, file handles and semaphore slots are released on success *and* on error.
4. **Know where the CPU goes.** JSON, crypto and ORM libraries often cost more than your own code: here the bcrypt library decided `/hash` (1.8× between libraries) and the JSON library decided `/json/transform` (2× between Rust and Go). Idioms count too: one Rust iterator made a loop 1.5× slower ([README, findings 1–3](../README.md#what-the-numbers-show)).
5. **No N+1 queries:** one query per list, not one per item. Check every query's plan with `EXPLAIN`.
6. **Validate at the edge.** Reject bad input with a 400 before it costs CPU or a database round trip.

## 3. Before deploying: check the defaults (the trap list)

Check each one in the real container or VM, not on a development machine.

| Check | How | Why |
|---|---|---|
| Open-file limit | `grep 'open files' /proc/<pid>/limits` | One file descriptor per connection; containers start processes at a soft limit of 1,024. [The Rust API failed past ~1,000 connections](../README.md#what-the-numbers-show) until it raised the limit. |
| CPU count the runtime sees | log it at startup (`runtime.GOMAXPROCS(0)`, `Environment.ProcessorCount`, `os.availableParallelism()`) | Thread pools are sized from it; a runtime that ignores the container limit oversubscribes the CPU. |
| Heap limit vs container memory limit | Node.js `--max-old-space-size`, Java `-Xmx` / `MaxRAMPercentage`, .NET `GCHeapHardLimit`, Go `GOMEMLIMIT` | Without it the runtime does not collect garbage before the kernel kills it (exit code 137, no error in the application's logs). |
| Pool size × instances | compare with the database's `max_connections` | New instances fail to connect during scale-out or deploys. |
| CPU limit vs pinning | watch `container_cpu_cfs_throttled_seconds_total` | A CPU quota (Kubernetes CPU limit) pauses the process when it uses its share early, adding up to ~100 ms to some requests. |
| Graceful shutdown | send SIGTERM during a load test | In-flight requests finish and nothing is lost on every deploy. |
| Liveness vs readiness probes | probe paths and timings | Traffic arrives only when the instance is ready; a slow dependency does not get the instance restarted in a loop. |
| Log level and log output | production config | Debug or synchronous file logging can halve throughput. |
| Accept queue | `sysctl net.core.somaxconn` and the server's backlog | Bursts of new connections after deploys or network blips. |

## 4. Measuring: the load test

1. **Prove correctness first.** A fast wrong answer is worthless ([parity check](../tools/parity.py)).
2. **Isolate the machine.** The load generator must not share the server's CPU, and its own CPU is checked too: on the laptop, [k6 was the bottleneck on `/health`](../results/2026-11-06-laptop/RESULTS.md).
3. **Warm up, then repeat at least 3 times.** Report the median and the spread; a difference smaller than the spread is noise.
4. **Use both load models.** Closed (fixed number of clients) answers "how much can it take?"; open (fixed request rate) answers "how fast is it at normal load?". Latency from a closed model hides queueing.
5. **Read p99 and the error rate, not the average.** The average hides the slowest 1%, which is what users notice.
6. **Find the knee.** Raise the load step by step until p99 jumps; safe capacity is about 70% of that point.
7. **Record CPU time per request and memory**, not only requests/s. They tell you the cost, even when something else is the bottleneck.
8. **Run the three special tests:**
   - **Spike:** a sudden burst of new connections ([found the open-file bug](../README.md#what-the-numbers-show)).
   - **Soak:** an hour or more at normal load; memory that keeps growing is a leak.
   - **Failure:** stop the database or a dependency; errors are clean, the service recovers by itself, nothing is lost.

## 5. In production: what to watch

| Signal | Watch | Alert when |
|---|---|---|
| Traffic | requests/s | a sudden drop (often an upstream failure) |
| Errors | 5xx rate, timeouts | above ~1% for 5 minutes |
| Latency | p50 and p99 | p99 above the target for 5–10 minutes |
| Saturation | CPU and CPU throttling, memory vs limit, pool wait time, queue depth, open files vs limit | above 70–80% |
| Runtime | GC pause time, Node.js event-loop delay, thread-pool queue length | event-loop delay above 100 ms |
| Restarts | container restarts, OOM kills | any OOM kill |

For every resource (CPU, memory, disk, network, connections), check **U**tilization (how busy), **S**aturation (how much is waiting) and **E**rrors: the USE method.

## 6. When something is slow

1. **Which kind of time is it?** CPU-bound (CPU at 100%) or waiting (CPU low, latency high)? The answer decides everything after.
2. **Find the saturated resource** with USE: CPU, memory, a pool, the database, the network, or the load generator.
3. **Profile instead of guessing.** Flame graphs: Go `pprof`; .NET `dotnet-trace` and `dotnet-counters`; Node.js `--cpu-prof` or clinic.js; Rust `cargo flamegraph`; any language on Linux `perf`.
4. **Change one thing, then re-run the same measurement.** Here the first fix (a larger accept queue) stopped the dropped connections but not the hanging requests; measuring again found the real cause.
5. **Write down what you found.** It becomes the postmortem, the fix's commit message and a good interview story.
