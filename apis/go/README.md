# Go (Gin)

Go 1.27, Gin 1.12, pgx 5 (`pgxpool`, 20 connections), `golang.org/x/crypto/bcrypt`.

- One goroutine per request (Go's `net/http` model under Gin). Blocking work such as bcrypt and the sieve runs on that goroutine; the Go scheduler spreads goroutines across all CPUs the container may use.
- `GOMAXPROCS` is left at its default, which since Go 1.25 follows the container's CPU limit.
- `/fanout` starts 10 goroutines with `sync.WaitGroup.Go` and waits for all of them.
- The binary is static (`CGO_ENABLED=0`) and runs on distroless `static-debian13` as a non-root user.

Run alone: `docker compose up -d postgres go`, then `curl localhost:8001/health`.
