package main

import (
	"cmp"
	"net/http"
	"slices"
	"strconv"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/jackc/pgx/v5/pgxpool"
	"golang.org/x/crypto/bcrypt"
)

const (
	bcryptCost     = 10
	maxPasswordLen = 72
	defaultPrimesN = 1_000_000
	maxPrimesN     = 10_000_000
	defaultLimit   = 50
	maxLimit       = 1000
	fanoutCalls    = 10
	fanoutDelay    = 50 * time.Millisecond
)

type handlers struct {
	db *pgxpool.Pool
}

func badRequest(c *gin.Context, msg string) {
	c.JSON(http.StatusBadRequest, gin.H{"error": msg})
}

// intQuery reads an optional integer query parameter within [lo, hi].
func intQuery(c *gin.Context, name string, def, lo, hi int) (int, bool) {
	raw, ok := c.GetQuery(name)
	if !ok {
		return def, true
	}
	v, err := strconv.Atoi(raw)
	if err != nil || v < lo || v > hi {
		return 0, false
	}
	return v, true
}

func (h *handlers) health(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (h *handlers) hash(c *gin.Context) {
	var req struct {
		Password string `json:"password"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		badRequest(c, "invalid JSON body")
		return
	}
	if req.Password == "" || len(req.Password) > maxPasswordLen {
		badRequest(c, "password must be 1-72 bytes")
		return
	}
	hashed, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcryptCost)
	if err != nil {
		c.Status(http.StatusInternalServerError)
		return
	}
	c.JSON(http.StatusOK, gin.H{"hash": string(hashed)})
}

type order struct {
	ID       int64    `json:"id"`
	Customer string   `json:"customer"`
	Category string   `json:"category"`
	Amount   float64  `json:"amount"`
	Quantity int64    `json:"quantity"`
	Tags     []string `json:"tags"`
}

type categoryStats struct {
	Category  string  `json:"category"`
	Count     int     `json:"count"`
	Revenue   float64 `json:"revenue"`
	AvgAmount float64 `json:"avgAmount"`
}

type topOrder struct {
	ID       int64   `json:"id"`
	Customer string  `json:"customer"`
	Revenue  float64 `json:"revenue"`
}

func (h *handlers) transform(c *gin.Context) {
	var req struct {
		Orders []order `json:"orders"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		badRequest(c, "invalid JSON body")
		return
	}

	type agg struct {
		count           int
		revenue, amount float64
	}
	groups := map[string]*agg{}
	tagCounts := map[string]int{}
	top := make([]topOrder, 0, len(req.Orders))
	matched := 0

	for _, o := range req.Orders {
		if !(o.Amount > 50) {
			continue
		}
		matched++
		revenue := o.Amount * float64(o.Quantity)
		g := groups[o.Category]
		if g == nil {
			g = &agg{}
			groups[o.Category] = g
		}
		g.count++
		g.revenue += revenue
		g.amount += o.Amount
		for _, t := range o.Tags {
			tagCounts[t]++
		}
		top = append(top, topOrder{ID: o.ID, Customer: o.Customer, Revenue: revenue})
	}

	slices.SortFunc(top, func(a, b topOrder) int {
		return cmp.Or(cmp.Compare(b.Revenue, a.Revenue), cmp.Compare(a.ID, b.ID))
	})
	top = top[:min(len(top), 10)]

	categories := make([]categoryStats, 0, len(groups))
	for name, g := range groups {
		categories = append(categories, categoryStats{name, g.count, g.revenue, g.amount / float64(g.count)})
	}
	slices.SortFunc(categories, func(a, b categoryStats) int {
		return cmp.Or(cmp.Compare(b.Revenue, a.Revenue), cmp.Compare(a.Category, b.Category))
	})

	c.JSON(http.StatusOK, gin.H{
		"totalOrders":   len(req.Orders),
		"matchedOrders": matched,
		"categories":    categories,
		"topOrders":     top,
		"tagCounts":     tagCounts,
	})
}

func (h *handlers) primes(c *gin.Context) {
	n, ok := intQuery(c, "n", defaultPrimesN, 0, maxPrimesN)
	if !ok {
		badRequest(c, "n must be an integer from 0 to 10000000")
		return
	}
	count, largest := sieve(n)
	c.JSON(http.StatusOK, gin.H{"n": n, "count": count, "largest": largest})
}

// sieve returns how many primes are <= n and the largest of them.
func sieve(n int) (count, largest int) {
	if n < 2 {
		return 0, 0
	}
	composite := make([]bool, n+1)
	for i := 2; i*i <= n; i++ {
		if !composite[i] {
			for j := i * i; j <= n; j += i {
				composite[j] = true
			}
		}
	}
	for k := 2; k <= n; k++ {
		if !composite[k] {
			count++
			largest = k
		}
	}
	return count, largest
}

type user struct {
	ID        int32     `json:"id"`
	Name      string    `json:"name"`
	Email     string    `json:"email"`
	CreatedAt time.Time `json:"createdAt"`
}

func (h *handlers) users(c *gin.Context) {
	limit, ok := intQuery(c, "limit", defaultLimit, 1, maxLimit)
	if !ok {
		badRequest(c, "limit must be an integer from 1 to 1000")
		return
	}
	rows, err := h.db.Query(c.Request.Context(),
		"SELECT id, name, email, created_at FROM users ORDER BY id LIMIT $1", limit)
	if err != nil {
		c.Status(http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	users := make([]user, 0, limit)
	for rows.Next() {
		var u user
		if err := rows.Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt); err != nil {
			c.Status(http.StatusInternalServerError)
			return
		}
		u.CreatedAt = u.CreatedAt.UTC()
		users = append(users, u)
	}
	if rows.Err() != nil {
		c.Status(http.StatusInternalServerError)
		return
	}
	c.JSON(http.StatusOK, users)
}

type callResult struct {
	Service string `json:"service"`
	Status  string `json:"status"`
}

// fanout simulates calling 10 downstream services in parallel, 50 ms each.
func (h *handlers) fanout(c *gin.Context) {
	results := make([]callResult, fanoutCalls)
	var wg sync.WaitGroup
	for i := range fanoutCalls {
		wg.Go(func() {
			time.Sleep(fanoutDelay)
			results[i] = callResult{Service: "service-" + strconv.Itoa(i), Status: "ok"}
		})
	}
	wg.Wait()
	c.JSON(http.StatusOK, gin.H{"calls": fanoutCalls, "results": results})
}
