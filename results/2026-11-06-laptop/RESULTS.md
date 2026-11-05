# Results: 2026-11-06-laptop

| Host | |
|---|---|
| CPU | Intel(R) Core(TM) i5-7200U CPU @ 2.50GHz (4 logical CPUs) |
| Memory | 7.1 GiB |
| Kernel, Docker | 7.0.0-42-generic, Docker 29.9.1 |
| CPU pinning | API `0,2`, Postgres `1,3`, k6 `1,3` (profile `laptop`) |
| Throughput phase | 3 × 20s per endpoint after a 10s warm-up |
| Latency phase | 30s per endpoint at 50% of the slowest API's median throughput, after a 10s warm-up |
| Date | 2026-11-06T01:09:00+06:00 |

## Overview

Median throughput in requests/s (higher is better):

| Endpoint | Go | Rust | .NET | Node.js |
|---|---:|---:|---:|---:|
| `health` | 13,049 | 13,347 | 11,959 | 12,819 |
| `hash` | 26.2 | 26.1 | 14.8 | 27.9 |
| `json` | 77.9 | 153 | 115 | 119 |
| `primes` | 308 | 237 | 298 | 218 |
| `users` | 4,430 | 3,867 | 3,397 | 2,538 |
| `fanout` | 4,167 | 4,091 | 4,637 | 4,001 |

p99 latency in ms at the same fixed rate for every API (lower is better):

| Endpoint | Rate (req/s) | Go | Rust | .NET | Node.js |
|---|---:|---:|---:|---:|---:|
| `health` | 5,979 | 168 | 152 ⚠️ | 169 | 166 |
| `hash` | 7 | 106 | 110 | 121 | 102 |
| `json` | 38 | 49.5 | 40.6 | 45.7 | 33.4 |
| `primes` | 108 | 25.2 | 26.4 | 24.6 | 21.4 |
| `users` | 1,268 | 3.18 | 3.21 | 10.2 | 4.45 |
| `fanout` | 2,000 | 99.8 | 76.4 | 67.1 | 110 |

⚠️ Requests failed, or more than 1% of the scheduled requests could not be sent: the p99 only covers the requests that completed. See the endpoint's table.

## `GET /health`

Framework overhead.

![GET /health results](charts/health.svg)

| API | Req/s median (min–max) | p50 / p99 ms at max load | Errors | p50 / p95 / p99 / max ms at fixed rate | Dropped | Peak MiB | API CPU % | k6 CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Go (Gin) | 13,049 (12,840–13,092) | 3.89 / 20.9 | 0.00% | 0.12 / 88.6 / 168 / 242 | 24 | 57 | 97 | 187 ⚠️ load-generator bound |
| Rust (Axum) | 13,347 (13,282–13,360) | 3.80 / 20.5 | 0.23% | 0.14 / 87.4 / 152 / 230 | 2,216 | 37 | 45 | 200 ⚠️ load-generator bound |
| .NET (ASP.NET Core) | 11,959 (11,864–12,510) | 4.20 / 22.5 | 0.00% | 0.28 / 80.1 / 169 / 360 | 865 | 93 | 165 | 189 ⚠️ load-generator bound |
| Node.js (NestJS) | 12,819 (12,735–12,829) | 3.97 / 21.5 | 0.00% | 0.19 / 84.7 / 166 / 252 | 0 | 119 | 68 | 199 ⚠️ load-generator bound |

⚠️ k6 averaged over 90% of its 2 CPUs: the load generator, not the API, may have set the ceiling for the marked rows.

## `POST /hash`

Bcrypt, cost 10: CPU in a library.

![POST /hash results](charts/hash.svg)

| API | Req/s median (min–max) | p50 / p99 ms at max load | Errors | p50 / p95 / p99 / max ms at fixed rate | Dropped | Peak MiB | API CPU % | k6 CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Go (Gin) | 26.2 (26.2–26.2) | 1,205 / 1,612 | 0.00% | 83.4 / 105 / 106 / 107 | 0 | 52 | 195 | 1 |
| Rust (Axum) | 26.1 (26.1–26.1) | 1,221 / 1,295 | 0.00% | 86.2 / 109 / 110 / 112 | 0 | 32 | 201 | 1 |
| .NET (ASP.NET Core) | 14.8 (14.8–14.8) | 2,070 / 3,834 | 0.00% | 93.4 / 119 / 121 / 126 | 0 | 95 | 198 | 1 |
| Node.js (NestJS) | 27.9 (27.7–27.9) | 1,147 / 1,162 | 0.00% | 79.7 / 98.3 / 102 / 107 | 0 | 104 | 201 | 1 |

## `POST /json/transform`

Parse and aggregate 574 KB of JSON.

![POST /json/transform results](charts/json.svg)

| API | Req/s median (min–max) | p50 / p99 ms at max load | Errors | p50 / p95 / p99 / max ms at fixed rate | Dropped | Peak MiB | API CPU % | k6 CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Go (Gin) | 77.9 (77.6–78.3) | 192 / 455 | 0.00% | 15.2 / 40.4 / 49.5 / 93.7 | 0 | 55 | 197 | 10 |
| Rust (Axum) | 153 (153–154) | 103 / 168 | 0.00% | 10.7 / 31.4 / 40.6 / 69.1 | 0 | 37 | 201 | 24 |
| .NET (ASP.NET Core) | 115 (114–115) | 133 / 244 | 0.00% | 11.2 / 37.5 / 45.7 / 99.0 | 0 | 106 | 199 | 16 |
| Node.js (NestJS) | 119 (108–119) | 120 / 258 | 0.00% | 10.6 / 28.1 / 33.4 / 41.8 | 0 | 159 | 118 | 15 |

## `GET /compute/primes?n=1000000`

Sieve: tight loops over memory.

![GET /compute/primes?n=1000000 results](charts/primes.svg)

| API | Req/s median (min–max) | p50 / p99 ms at max load | Errors | p50 / p95 / p99 / max ms at fixed rate | Dropped | Peak MiB | API CPU % | k6 CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Go (Gin) | 308 (307–309) | 92.1 / 320 | 0.00% | 5.69 / 21.1 / 25.2 / 40.4 | 0 | 27 | 197 | 12 |
| Rust (Axum) | 237 (236–249) | 138 / 207 | 0.00% | 4.55 / 18.5 / 26.4 / 44.3 | 0 | 32 | 198 | 9 |
| .NET (ASP.NET Core) | 298 (296–301) | 103 / 174 | 0.00% | 4.27 / 17.3 / 24.6 / 33.2 | 0 | 99 | 193 | 7 |
| Node.js (NestJS) | 218 (216–218) | 140 / 287 | 0.00% | 4.73 / 17.0 / 21.4 / 43.9 | 0 | 161 | 124 | 10 |

## `GET /users?limit=50`

Postgres query + row mapping.

![GET /users?limit=50 results](charts/users.svg)

| API | Req/s median (min–max) | p50 / p99 ms at max load | Errors | p50 / p95 / p99 / max ms at fixed rate | Dropped | Peak MiB | API CPU % | k6 CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Go (Gin) | 4,430 (4,424–4,431) | 12.6 / 40.7 | 0.00% | 0.64 / 1.09 / 3.18 / 18.5 | 0 | 30 | 125 | 97 |
| Rust (Axum) | 3,867 (3,848–3,871) | 15.1 / 44.6 | 0.00% | 0.67 / 1.24 / 3.21 / 13.4 | 0 | 34 | 112 | 84 |
| .NET (ASP.NET Core) | 3,397 (3,391–3,408) | 17.3 / 48.9 | 0.00% | 0.71 / 1.91 / 10.2 / 46.8 | 0 | 102 | 179 | 80 |
| Node.js (NestJS) | 2,538 (2,390–2,647) | 23.3 / 53.0 | 0.00% | 1.01 / 2.21 / 4.45 / 33.6 | 0 | 167 | 102 | 81 |

## `GET /fanout`

10 parallel 50 ms waits per request.

![GET /fanout results](charts/fanout.svg)

| API | Req/s median (min–max) | p50 / p99 ms at max load | Errors | p50 / p95 / p99 / max ms at fixed rate | Dropped | Peak MiB | API CPU % | k6 CPU % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Go (Gin) | 4,167 (4,076–4,224) | 52.4 / 129 | 0.00% | 51.2 / 70.4 / 99.8 / 161 | 0 | 39 | 99 | 132 |
| Rust (Axum) | 4,091 (4,053–4,173) | 53.6 / 114 | 0.00% | 51.5 / 53.3 / 76.4 / 137 | 0 | 35 | 81 | 152 |
| .NET (ASP.NET Core) | 4,637 (4,544–4,685) | 51.3 / 105 | 0.00% | 50.6 / 53.3 / 67.1 / 105 | 0 | 133 | 128 | 103 |
| Node.js (NestJS) | 4,001 (3,788–4,396) | 57.1 / 110 | 0.00% | 51.1 / 64.4 / 110 / 186 | 0 | 148 | 76 | 120 |

## Footprint

| API | Image size (MB) | Startup to first 200 (ms) | Memory after start |
|---|---:|---:|---:|
| Go (Gin) | 40.7 | 1,040 | 2.215MiB |
| Rust (Axum) | 46.8 | 1,092 | 900KiB |
| .NET (ASP.NET Core) | 184.0 | 1,510 | 20.88MiB |
| Node.js (NestJS) | 394.6 | 1,890 | 50.62MiB |

CPU % is from `docker stats`: 100% = one logical CPU fully busy. Startup is measured from `docker compose up` to the first successful `/health`, so it includes container creation.
