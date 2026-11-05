// Command go-api serves the benchmark contract (docs/design.md, section 2) with Gin.
package main

import (
	"context"
	"log"
	"net/http"
	"os"

	"github.com/gin-gonic/gin"
	"github.com/jackc/pgx/v5/pgxpool"
)

const maxBodyBytes = 10 << 20

func main() {
	gin.SetMode(gin.ReleaseMode)

	cfg, err := pgxpool.ParseConfig(os.Getenv("DATABASE_URL"))
	if err != nil {
		log.Fatalf("invalid DATABASE_URL: %v", err)
	}
	cfg.MaxConns = 20
	pool, err := pgxpool.NewWithConfig(context.Background(), cfg)
	if err != nil {
		log.Fatalf("connect to Postgres: %v", err)
	}
	defer pool.Close()

	r := gin.New() // no request logger
	r.Use(gin.Recovery(), func(c *gin.Context) {
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxBodyBytes)
		c.Next()
	})

	h := &handlers{db: pool}
	r.GET("/health", h.health)
	r.POST("/hash", h.hash)
	r.POST("/json/transform", h.transform)
	r.GET("/compute/primes", h.primes)
	r.GET("/users", h.users)
	r.GET("/fanout", h.fanout)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	log.Printf("Go (Gin) listening on :%s", port)
	if err := r.Run(":" + port); err != nil {
		log.Fatal(err)
	}
}
