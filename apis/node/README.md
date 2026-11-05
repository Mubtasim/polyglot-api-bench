# Node.js (NestJS on Fastify)

Node.js 26 LTS, NestJS 12 with the Fastify adapter, `pg` 8 (20 connections), the native `bcrypt` package, TypeScript 7, ES modules.

- One process, one **event loop**: all JavaScript runs on a single thread. Waiting (Postgres, timers) costs nothing, but CPU work blocks every other request until it finishes. The sieve and the JSON transform run on the event loop, as typical handlers would.
- `bcrypt` is a native C++ module that hashes on **libuv's thread pool** (4 threads by default), so hashing does not block the event loop and can use more than one CPU.
- Errors from Nest and from Fastify (e.g. a body that is not valid JSON) go through one exception filter that returns the contract's `{"error": "..."}`.
- The usual way to use more cores is more processes (Node's cluster module, PM2, or more containers). That variant is left out on purpose: this measures one process, the way most NestJS services are deployed in containers.

Run alone: `docker compose up -d postgres node`, then `curl localhost:8004/health`.
