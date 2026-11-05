# Rust (Axum)

Rust 1.99 (edition 2024), Axum 0.8 on Tokio, SQLx 0.9 (20 connections), the `bcrypt` crate.

- Tokio runs async tasks on one worker thread per CPU the container may use. A task keeps its thread until it reaches an `.await`.
- bcrypt runs on Tokio's blocking pool (`spawn_blocking`): tens of milliseconds of CPU on a worker thread would stall every other task on it. The sieve and the JSON transform run inline, as a typical handler would; they take a few milliseconds.
- `/json/transform` borrows strings from the parsed request instead of copying them (`&str` fields in the response types).
- `/fanout` builds 10 futures and awaits them together with `join_all`, all on one task.
- Release build with link-time optimization (`lto = true`, `codegen-units = 1`); runs on distroless `cc-debian13` as a non-root user.

Run alone: `docker compose up -d postgres rust`, then `curl localhost:8002/health`.
