# How an API uses CPU, threads and memory

Background for reading the results. Each section ends with the rough numbers worth remembering. The numbers here are estimates; the benchmark measures the real ones, and [the results](../results/) replace any estimate that turns out wrong.

The reference machine is the laptop the first run used: Intel i5-7200U (2 cores, 4 threads), 8 GB RAM.

## 1. Cores, hardware threads and logical CPUs

A **physical core** is a complete processor: it fetches instructions and has its own units for arithmetic, memory access and branching. With **hyper-threading** (Intel's name; AMD calls it SMT), one core runs **two hardware threads**. The core keeps two sets of registers and switches between the two threads whenever one stalls, for example while waiting for memory. The two threads **share** the core's arithmetic units, so two busy threads on one core get roughly **1.2–1.3×** the work of one thread, not 2×.

The operating system shows each hardware thread as a **logical CPU** and numbers them from 0. `lscpu -e` shows which logical CPU sits on which core. On the reference laptop:

```
CPU  CORE
  0     0     ┐ physical core 0
  2     0     ┘
  1     1     ┐ physical core 1
  3     1     ┘
```

**Pinning.** Docker's `cpuset: "0,2"` lets a container run only on logical CPUs 0 and 2, which here is all of physical core 0. The benchmark gives the API one whole core and puts Postgres and the load generator on the other, so they never compete with the API for the same arithmetic units. Pinning the API to `0,1` instead would give it half of each core, and the load generator would share both of them.

**Pinning vs a CPU quota.** Docker's `cpus: 2` (and a Kubernetes CPU limit) works differently: the container may use 200 ms of CPU time per 100 ms period, on any CPU. A busy multi-threaded process can use its whole quota early in a period and then gets **throttled** (paused) until the next period starts. That adds up to ~100 ms to some requests even though the machine has idle CPUs. This is a common cause of mysterious p99 latency in Kubernetes.

**Remember**
- Logical CPUs = cores × 2 with hyper-threading; a second thread on a core adds ~20–30%, not 100%.
- `cpuset` = which CPUs; `cpus`/quota = how much time. Quotas can throttle; pinning cannot.

## 2. How a request uses CPU

A request's **wall time** (what the client waits) has two parts: **CPU time** (instructions actually running) and **waiting** (for the database, the network, a timer or a lock). A thread that is waiting uses no CPU; the operating system parks it and runs something else.

That splits endpoints into two kinds with different maths.

**CPU-bound** endpoints spend most of their time computing. Their ceiling is

> max requests/s ≈ CPUs available ÷ CPU time per request

| Endpoint | CPU per request (estimate) | Ceiling on one core (2 hardware threads) |
|---|---|---|
| `/hash` (bcrypt cost 10) | 50–100 ms | ~12–25/s |
| `/compute/primes` (n = 1,000,000) | 2–10 ms | ~120–600/s |
| `/json/transform` (574 KB) | 5–20 ms | ~60–250/s |

No framework can beat this ceiling; they differ in how fast their code runs and whether they can use every CPU they are given.

bcrypt is slow **on purpose**: cost 10 means 2¹⁰ rounds of an expensive key setup, so an attacker who steals the hashes needs about as long per guess. Each +1 in cost doubles the time. That is why login endpoints are among the most CPU-heavy parts of real web apps.

**I/O-bound** endpoints mostly wait. Their throughput follows **Little's law**:

> requests/s = requests in flight ÷ time per request

`/fanout` waits 50 ms per request. With 256 clients sending one request at a time, the most it can do is 256 ÷ 0.05 s ≈ **5,100 requests/s**, almost without using any CPU. Adding CPUs does not help; more requests in flight does. `/users` is mostly waiting too (a query takes ~0.5–2 ms), but serializing the 50 rows to JSON costs some CPU on top.

**Remember**
- CPU-bound: requests/s ≈ CPUs ÷ CPU time per request.
- I/O-bound: requests/s ≈ in flight ÷ latency (Little's law).
- Before scaling anything, find out which kind of time the request is spending.

## 3. Threads and how each runtime runs requests

A **thread** is one sequence of instructions the operating system can schedule on a CPU. A server needs many requests in progress at once, but only as many can *run* at a time as there are CPUs. Each runtime solves this differently, and the differences explain most of the results.

| Runtime | Model | What happens with CPU-heavy work | Threads |
|---|---|---|---|
| **Node.js** | One **event loop** thread runs all JavaScript. Waiting is handed to the operating system, and a callback runs when the answer arrives. | It **blocks every other request** until it finishes: while one sieve runs, even `/health` waits. Native modules like `bcrypt` escape by running on **libuv's thread pool** (4 threads by default). | 1 for JavaScript, 4 in the libuv pool, a few for V8's garbage collector |
| **Go** | **Goroutines** (start at a few KB) multiplexed onto one OS thread per CPU (`GOMAXPROCS`). | Runs on whichever CPU is free; the scheduler preempts long-running goroutines so others still get a turn. | ≈ CPUs, plus extra threads parked in system calls |
| **Rust (Tokio)** | **Async tasks** on one worker thread per CPU. A task runs until it reaches an `.await`. | It **stalls its worker thread** until done, so slow work is moved to Tokio's separate blocking pool with `spawn_blocking`. | ≈ CPUs + blocking pool (grows up to 512 on demand) |
| **.NET** | **async/await** on a shared **thread pool**. | Runs on a pool thread; if many are blocked, the pool slowly adds threads. | Starts at ≈ CPUs, grows when threads block |

**Context switches.** When a CPU switches from one thread to another, it saves and restores registers and loses warm caches, costing ~1–5 µs. That is why runtimes keep about one busy thread per CPU instead of one thread per request: 10,000 OS threads would spend a lot of time switching. Goroutines and async tasks switch inside the process, for about 100–200 ns.

**Remember**
- Node.js: perfect for waiting, but one thread for computing. To use more cores, run more processes (cluster mode or more containers).
- Go, Rust and .NET use every CPU automatically, as long as the runtime knows how many it has (all three read the container's limits).
- Async code must never block: in Node.js it stalls the whole server, in Tokio one worker thread.

## 4. Memory

A server's memory (its **resident set**, what `docker stats` shows) comes from four places.

**1. The runtime's baseline**, before any request:

| Runtime | Idle memory (estimate) | Why |
|---|---|---|
| Rust | ~5–10 MB | Machine code, no runtime beyond a small async executor |
| Go | ~10–20 MB | Machine code + garbage collector and scheduler (measured: 17 MB) |
| .NET | ~40–80 MB | JIT compiler, compiled code, type metadata, GC heaps |
| Node.js | ~50–90 MB | V8 engine, JIT compiler, NestJS's loaded modules |

**2. Data per request × requests in flight.** Parsed JSON is much bigger than its text: every object and string carries headers and pointers. The 574 KB `/json/transform` body becomes roughly **3–5 MB** of objects, so 16 concurrent requests hold ~50–80 MB at once. The sieve for n = 1,000,000 allocates 1 MB per request (one byte per number), so 32 at once is ~32 MB.

**3. Garbage-collector headroom.** Go, .NET and Node.js do not free memory the moment it is unused; they collect in batches, which is faster but needs room:
- **Go** lets the heap grow to about **2× the live data** before collecting (`GOGC=100`).
- **.NET server GC** keeps one heap per CPU and collects less often, trading memory for throughput (since .NET 9 it also shrinks the heaps when load drops).
- **V8** (Node.js) divides the heap into a small young generation, collected often, and an old generation, collected rarely.
- **Rust** has no collector: memory is freed when its owner goes out of scope, so peak memory sits close to live data.

**4. Threads.** Each OS thread reserves an 8 MB stack, but only the pages it actually touches use RAM, usually tens of KB. A goroutine's stack starts at a few KB and grows as needed. So 10,000 goroutines cost tens of MB, while 10,000 OS threads could cost hundreds.

**The database counts too.** Postgres keeps a shared page cache (`shared_buffers`, 128 MB by default) plus roughly 5–10 MB per connection. Each API holds up to 20 connections, which is why connection pools are sized small and shared, and why many servers × big pools can exhaust a database.

**The 1 GB cap.** Each API container may use at most 1 GB. If it goes over, the kernel's **OOM killer** stops the process at once (exit code 137), with no exception or log line from the application.

**Remember**
- Memory ≈ baseline + (per-request data × in flight) + GC headroom.
- Parsed JSON is ~5–10× its text size.
- Rust < Go < .NET ≈ Node.js at idle; under load the gap depends on the garbage collector.

## 5. Building is a workload too

Compilers are often the most memory-hungry thing on a developer laptop.

| Build | Disk (build image + cache) | Peak RAM | Time on 2 cores |
|---|---|---|---|
| Go | ~1 GB | ~0.7 GB | ~1.5 min |
| Rust (release, link-time optimization) | ~2 GB | 1.5–2.5 GB | 6–12 min |
| .NET | ~1.2 GB | 1–1.5 GB | 2–4 min |
| Node.js (TypeScript) | ~0.8 GB | ~0.7 GB | 1–2 min |

Rust's **link-time optimization** (`lto = true`, `codegen-units = 1`) optimizes the whole program in one go, so the whole program's intermediate code sits in memory at once. That makes a faster binary and a slow, memory-hungry build.

Building all four at once on a 2-core, 8 GB laptop would need ~5–6 GB of RAM at the same time, so the machine swaps or the kernel kills a build. It would not finish sooner either: four builds sharing two cores each run at about a quarter speed. Parallel work only pays off when the machine has spare capacity.

## 6. Estimating before measuring

A back-of-the-envelope estimate takes five minutes and catches most sizing mistakes before they reach production:

1. **Load:** peak requests/s. For example 1,000 logins/s at peak.
2. **CPU per request:** measure one request with a profiler or timer. A bcrypt login at ~70 ms.
3. **Cores needed:** load × CPU time = 1,000 × 0.07 s = **70 cores**, before any headroom. That number alone says "lower the bcrypt cost, cache sessions, or budget for a fleet".
4. **Concurrency:** load × latency (Little's law). At 100 ms per request, 100 requests are in flight; size thread pools, connection pools and memory for that.
5. **Memory:** baseline + per-request memory × in-flight requests + GC headroom.
6. **Headroom:** plan to run at ~50–70% of the ceiling, so traffic spikes and failovers fit.

Then **measure**, because estimates are often off by 2–3×: load-test at the expected rate, watch CPU, memory, p99 latency and errors, and correct the model. This benchmark is exactly that loop at small scale.

## 7. Numbers to keep in your head

| What | Roughly |
|---|---|
| Main-memory access | ~100 ns |
| OS thread context switch | 1–5 µs |
| Goroutine or async-task switch | 100–200 ns |
| Read 1 MB sequentially from memory | ~50–250 µs |
| Simple Postgres query on the same machine | 0.2–1 ms |
| Round trip within one data center | ~0.5 ms |
| bcrypt, cost 10 | 50–100 ms |
| Parse 1 MB of JSON | 5–20 ms |
| Idle memory: Rust / Go / .NET / Node.js | ~5 / ~15 / ~60 / ~70 MB |
| Postgres memory per connection | 5–10 MB |
| Hyper-threading gain | +20–30% |
| Little's law | in flight = rate × latency |
