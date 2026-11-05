#!/usr/bin/env python3
"""Writes the /json/transform request body: a fixed list of orders.

The generator is deterministic (Park-Miller, seed 42), so every run and every
API receives exactly the same bytes. 5,000 orders is about 600 KB.
"""
import argparse
import json
import pathlib

CATEGORIES = ["electronics", "books", "clothing", "home", "sports",
              "toys", "beauty", "grocery", "automotive", "garden"]
TAGS = ["sale", "new", "popular", "limited", "gift",
        "eco", "premium", "clearance", "bundle", "imported"]


def build_orders(count, seed=42):
    state = seed

    def rand():
        nonlocal state
        state = (state * 16807) % 2147483647
        return state / 2147483647

    orders = []
    for i in range(count):
        tags = [TAGS[int(rand() * len(TAGS))] for _ in range(1 + int(rand() * 4))]
        orders.append({
            "id": i + 1,
            "customer": f"customer-{int(rand() * 1000)}",
            "category": CATEGORIES[int(rand() * len(CATEGORIES))],
            "amount": round(rand() * 20000) / 100,
            "quantity": 1 + int(rand() * 10),
            "tags": tags,
        })
    return orders


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--out", default="bench/data/orders.json")
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"orders": build_orders(args.count)}, separators=(",", ":"))
    out.write_text(body)
    print(f"wrote {out} ({len(body) / 1024:.0f} KB, {args.count} orders)")


if __name__ == "__main__":
    main()
