#!/usr/bin/env python3
"""Checks that every API implements the contract in docs/design.md, section 2.

The expected answers come from a reference implementation in this file, not
from one of the APIs, so a bug shared by all four still fails.

    python3 tools/parity.py --api go=http://localhost:8001 --api rust=http://localhost:8002

Exits 1 if any check fails.
"""
import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import gen_payload

SEED_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
BCRYPT_RE = re.compile(r"^\$2[aby]\$10\$[./A-Za-z0-9]{53}$")


# ---------- reference implementation ----------

def expected_transform(orders):
    groups, tags, top = {}, {}, []
    matched = 0
    for o in orders:
        if not o["amount"] > 50:
            continue
        matched += 1
        revenue = o["amount"] * o["quantity"]
        g = groups.setdefault(o["category"], {"count": 0, "revenue": 0.0, "amount": 0.0})
        g["count"] += 1
        g["revenue"] += revenue
        g["amount"] += o["amount"]
        for t in o["tags"]:
            tags[t] = tags.get(t, 0) + 1
        top.append({"id": o["id"], "customer": o["customer"], "revenue": revenue})

    top.sort(key=lambda t: (-t["revenue"], t["id"]))
    categories = [
        {"category": name, "count": g["count"], "revenue": g["revenue"], "avgAmount": g["amount"] / g["count"]}
        for name, g in groups.items()
    ]
    categories.sort(key=lambda c: (-c["revenue"], c["category"]))
    return {
        "totalOrders": len(orders),
        "matchedOrders": matched,
        "categories": categories,
        "topOrders": top[:10],
        "tagCounts": tags,
    }


def expected_primes(n):
    if n < 2:
        return {"n": n, "count": 0, "largest": 0}
    sieve = bytearray([1]) * (n + 1)
    sieve[0] = sieve[1] = 0
    i = 2
    while i * i <= n:
        if sieve[i]:
            sieve[i * i::i] = bytes(len(range(i * i, n + 1, i)))
        i += 1
    count = sum(sieve)
    largest = next(k for k in range(n, 1, -1) if sieve[k])
    return {"n": n, "count": count, "largest": largest}


def expected_user(user_id):
    return {
        "id": user_id,
        "name": f"User {user_id}",
        "email": f"user{user_id}@example.com",
        "createdAt": SEED_START + timedelta(minutes=user_id),
    }


# ---------- comparison ----------

def diff(got, want, path="$"):
    """Returns a list of differences. Numbers match within 1e-9 relative error."""
    if isinstance(want, bool) or isinstance(got, bool):
        return [] if got is want else [f"{path}: expected {want!r}, got {got!r}"]
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return [] if math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-9) else [f"{path}: expected {want}, got {got}"]
    if isinstance(want, dict) and isinstance(got, dict):
        out = [f"{path}: missing key {k!r}" for k in want if k not in got]
        out += [f"{path}: unexpected key {k!r}" for k in got if k not in want]
        for k in want:
            if k in got:
                out += diff(got[k], want[k], f"{path}.{k}")
        return out
    if isinstance(want, list) and isinstance(got, list):
        if len(got) != len(want):
            return [f"{path}: expected {len(want)} items, got {len(got)}"]
        out = []
        for i, (g, w) in enumerate(zip(got, want)):
            out += diff(g, w, f"{path}[{i}]")
        return out
    return [] if got == want else [f"{path}: expected {want!r}, got {got!r}"]


def parse_instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def same_instant(a, b):
    return parse_instant(a) == parse_instant(b)


# ---------- HTTP ----------

def request(base, method, path, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, text = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read()
    try:
        return status, json.loads(text) if text else None
    except ValueError:
        return status, text.decode(errors="replace")


# ---------- checks ----------

class Checker:
    def __init__(self, name, base):
        self.name, self.base, self.failures = name, base, []

    def fail(self, check, message):
        self.failures.append(f"{check}: {message}")

    def expect_status(self, check, status, want):
        if status != want:
            self.fail(check, f"expected HTTP {want}, got {status}")
            return False
        return True

    def expect_bad_request(self, check, method, path, body=None, raw=None):
        status, payload = request(self.base, method, path, body, raw)
        if self.expect_status(check, status, 400):
            if not (isinstance(payload, dict) and isinstance(payload.get("error"), str)):
                self.fail(check, f'400 body should be {{"error": "..."}}, got {payload!r}')

    def expect_json(self, check, status, payload, want):
        if self.expect_status(check, status, 200):
            for d in diff(payload, want)[:5]:
                self.fail(check, d)

    def health(self):
        status, body = request(self.base, "GET", "/health")
        self.expect_json("health", status, body, {"status": "ok"})

    def hash(self):
        hashes = []
        for password in ["correct-horse-battery-staple", "correct-horse-battery-staple", "a" * 72]:
            status, body = request(self.base, "POST", "/hash", {"password": password})
            if self.expect_status("hash", status, 200):
                h = body.get("hash") if isinstance(body, dict) else None
                if not (isinstance(h, str) and BCRYPT_RE.match(h)):
                    self.fail("hash", f"not a cost-10 bcrypt hash: {h!r}")
                hashes.append(h)
        if len(hashes) >= 2 and hashes[0] == hashes[1]:
            self.fail("hash", "two hashes of the same password are equal (salt not random)")
        self.expect_bad_request("hash: missing password", "POST", "/hash", {})
        self.expect_bad_request("hash: empty password", "POST", "/hash", {"password": ""})
        self.expect_bad_request("hash: 73-byte password", "POST", "/hash", {"password": "a" * 73})
        self.expect_bad_request("hash: invalid JSON", "POST", "/hash", raw=b"{not json")

    def transform(self, orders):
        cases = {
            "full payload": orders,
            "empty": [],
            "ties": [
                {"id": 2, "customer": "b", "category": "home", "amount": 120.0, "quantity": 1, "tags": ["x"]},
                {"id": 1, "customer": "a", "category": "books", "amount": 60.0, "quantity": 2, "tags": ["x", "y"]},
                {"id": 3, "customer": "c", "category": "toys", "amount": 50.0, "quantity": 5, "tags": ["z"]},
            ],
        }
        for label, case in cases.items():
            status, body = request(self.base, "POST", "/json/transform", {"orders": case})
            self.expect_json(f"json/transform ({label})", status, body, expected_transform(case))
        self.expect_bad_request("json/transform: invalid JSON", "POST", "/json/transform", raw=b"{not json")

    def primes(self):
        for n in [0, 1, 2, 100, 999_983, 10_000_000]:
            status, body = request(self.base, "GET", f"/compute/primes?n={n}")
            self.expect_json(f"primes n={n}", status, body, expected_primes(n))
        status, body = request(self.base, "GET", "/compute/primes")
        self.expect_json("primes default", status, body, expected_primes(1_000_000))
        for bad in ["-1", "10000001", "abc", "1.5"]:
            self.expect_bad_request(f"primes n={bad}", "GET", f"/compute/primes?n={bad}")

    def users(self):
        for query, count in [("", 50), ("?limit=1", 1), ("?limit=1000", 1000)]:
            status, body = request(self.base, "GET", "/users" + query)
            check = f"users{query or ' default'}"
            if not self.expect_status(check, status, 200):
                continue
            if not isinstance(body, list) or len(body) != count:
                self.fail(check, f"expected a list of {count} users, got {type(body).__name__} of {len(body) if isinstance(body, list) else '?'}")
                continue
            for i, row in enumerate(body):
                want = expected_user(i + 1)
                created = row.pop("createdAt", None) if isinstance(row, dict) else None
                problems = diff(row, {k: v for k, v in want.items() if k != "createdAt"}, f"$[{i}]")
                if not isinstance(created, str):
                    problems.append(f"$[{i}].createdAt: expected a timestamp string, got {created!r}")
                elif parse_instant(created) != want["createdAt"]:
                    problems.append(f"$[{i}].createdAt: expected {want['createdAt'].isoformat()}, got {created}")
                if problems:
                    self.fail(check, problems[0])
                    break
        for bad in ["0", "1001", "abc"]:
            self.expect_bad_request(f"users limit={bad}", "GET", f"/users?limit={bad}")

    def fanout(self):
        request(self.base, "GET", "/fanout")  # warm up
        start = time.perf_counter()
        status, body = request(self.base, "GET", "/fanout")
        elapsed_ms = (time.perf_counter() - start) * 1000
        want = {"calls": 10, "results": [{"service": f"service-{i}", "status": "ok"} for i in range(10)]}
        self.expect_json("fanout", status, body, want)
        if not 45 <= elapsed_ms < 200:
            self.fail("fanout", f"took {elapsed_ms:.0f} ms; 10 parallel 50 ms waits should take 50-200 ms")

    def run(self, orders):
        for check in (self.health, self.hash, lambda: self.transform(orders), self.primes, self.users, self.fanout):
            try:
                check()
            except (urllib.error.URLError, OSError) as e:
                self.fail(getattr(check, "__name__", "check"), f"request failed: {e}")
        return self.failures


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", action="append", required=True, metavar="NAME=URL")
    args = parser.parse_args()

    orders = gen_payload.build_orders(5000)
    failed = False
    for spec in args.api:
        name, _, base = spec.partition("=")
        failures = Checker(name, base.rstrip("/")).run(orders)
        if failures:
            failed = True
            print(f"FAIL {name}: {len(failures)} problem(s)")
            for f in failures:
                print(f"  - {f}")
        else:
            print(f"PASS {name}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
