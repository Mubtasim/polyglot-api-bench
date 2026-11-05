# .NET (ASP.NET Core Minimal APIs)

.NET 10, ASP.NET Core Minimal APIs on Kestrel, Npgsql 10 (`NpgsqlDataSource`, 20 connections), BCrypt.Net-Next.

- Requests run on the .NET thread pool, which starts with one thread per CPU and adds threads when they block. `async`/`await` frees the thread while waiting for Postgres or a timer.
- bcrypt runs synchronously on a thread-pool thread, the usual way to call BCrypt.Net.
- Garbage collection uses the ASP.NET Core default, **server GC** (one heap per CPU, fewer and larger collections), which trades memory for throughput.
- The JIT compiles methods on first use and recompiles hot ones with more optimization (tiered compilation), so .NET needs a warm-up before it reaches full speed; the benchmark discards a warm-up run for every endpoint.
- Binding failures (bad JSON, non-integer query values) throw and are turned into the contract's `{"error": "..."}` 400.
- Runs on the `aspnet` **chiseled** Ubuntu image (no shell or package manager) as a non-root user.

Run alone: `docker compose up -d postgres dotnet`, then `curl localhost:8003/health`.
