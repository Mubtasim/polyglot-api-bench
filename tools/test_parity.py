import unittest
from datetime import datetime, timezone

import gen_payload
import parity


class ReferenceTransform(unittest.TestCase):
    def test_filters_groups_and_ranks(self):
        orders = [
            {"id": 1, "customer": "a", "category": "books", "amount": 60.0, "quantity": 2, "tags": ["new", "new"]},
            {"id": 2, "customer": "b", "category": "books", "amount": 50.0, "quantity": 9, "tags": ["sale"]},
            {"id": 3, "customer": "c", "category": "home", "amount": 120.0, "quantity": 1, "tags": ["sale"]},
            {"id": 4, "customer": "d", "category": "toys", "amount": 80.0, "quantity": 1, "tags": []},
        ]
        got = parity.expected_transform(orders)
        self.assertEqual(got["totalOrders"], 4)
        self.assertEqual(got["matchedOrders"], 3)  # amount 50 is not > 50
        # books and home tie on revenue 120: category name breaks the tie
        self.assertEqual([c["category"] for c in got["categories"]], ["books", "home", "toys"])
        self.assertEqual(got["categories"][0], {"category": "books", "count": 1, "revenue": 120.0, "avgAmount": 60.0})
        # orders 1 and 3 tie on revenue 120: id breaks the tie
        self.assertEqual([o["id"] for o in got["topOrders"]], [1, 3, 4])
        self.assertEqual(got["tagCounts"], {"new": 2, "sale": 1})

    def test_top_orders_capped_at_ten(self):
        got = parity.expected_transform(gen_payload.build_orders(200))
        self.assertEqual(len(got["topOrders"]), 10)

    def test_empty(self):
        self.assertEqual(parity.expected_transform([]), {
            "totalOrders": 0, "matchedOrders": 0, "categories": [], "topOrders": [], "tagCounts": {}})


class ReferencePrimes(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(parity.expected_primes(0), {"n": 0, "count": 0, "largest": 0})
        self.assertEqual(parity.expected_primes(1), {"n": 1, "count": 0, "largest": 0})
        self.assertEqual(parity.expected_primes(2), {"n": 2, "count": 1, "largest": 2})
        self.assertEqual(parity.expected_primes(100), {"n": 100, "count": 25, "largest": 97})
        self.assertEqual(parity.expected_primes(1_000_000), {"n": 1_000_000, "count": 78498, "largest": 999983})


class ReferenceUsers(unittest.TestCase):
    def test_seed_formula(self):
        u = parity.expected_user(61)
        self.assertEqual(u["name"], "User 61")
        self.assertEqual(u["email"], "user61@example.com")
        self.assertEqual(u["createdAt"], datetime(2026, 1, 1, 1, 1, tzinfo=timezone.utc))


class Compare(unittest.TestCase):
    def test_numbers_within_tolerance_and_key_order_ignored(self):
        self.assertEqual(parity.diff({"a": 1, "b": [0.1 + 0.2]}, {"b": [0.3], "a": 1.0}), [])

    def test_reports_path_of_mismatch(self):
        self.assertEqual(parity.diff({"a": [1, 2]}, {"a": [1, 3]}), ["$.a[1]: expected 3, got 2"])

    def test_missing_and_extra_keys(self):
        self.assertEqual(sorted(parity.diff({"a": 1, "x": 2}, {"a": 1, "y": 2})),
                         ["$: missing key 'y'", "$: unexpected key 'x'"])

    def test_timestamps_compared_as_instants(self):
        self.assertTrue(parity.same_instant("2026-01-01T00:01:00Z", "2026-01-01T00:01:00.000+00:00"))
        self.assertFalse(parity.same_instant("2026-01-01T00:01:00Z", "2026-01-01T00:02:00Z"))


if __name__ == "__main__":
    unittest.main()
