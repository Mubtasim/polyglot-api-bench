import json
import pathlib
import tempfile
import unittest

import report


def k6_result(rps, p99=10.0, errors=0.0, dropped=0):
    return {"rps": rps, "errorRate": errors, "droppedIterations": dropped, "requests": rps * 20,
            "latencyMs": {"avg": 1, "p50": 1, "p95": 5, "p99": p99, "max": 50}}


class Parsing(unittest.TestCase):
    def test_mem_units(self):
        self.assertEqual(report.parse_mem("512KiB"), 0.5)
        self.assertEqual(report.parse_mem("12.5MiB"), 12.5)
        self.assertEqual(report.parse_mem("1.5GiB"), 1536)
        self.assertEqual(report.parse_mem("1048576B"), 1)
        self.assertEqual(report.parse_mem("12.5MB"), 12.5 * 1e6 / 1048576)

    def test_result_names(self):
        self.assertEqual(report.parse_name("go-hash-throughput-2"), ("go", "hash", "throughput", 2))
        self.assertEqual(report.parse_name("dotnet-users-latency"), ("dotnet", "users", "latency", None))
        self.assertIsNone(report.parse_name("node-startup-latency"))

    def test_stats_lines(self):
        lines = ["1700000000000,polyglot-api-bench-go-1,150.25%,20.5MiB",
                 "1700000001000,pab-k6,80.00%,100MiB",
                 "garbage line",
                 "1700000002000,polyglot-api-bench-go-1,170.75%,31MiB"]
        got = report.parse_stats(lines)
        self.assertEqual(got["api"], [(150.25, 20.5), (170.75, 31.0)])
        self.assertEqual(got["k6"], [(80.0, 100.0)])


class Rates(unittest.TestCase):
    def test_half_of_slowest_median(self):
        runs = {
            ("go", "hash"): [k6_result(30), k6_result(32), k6_result(31)],
            ("node", "hash"): [k6_result(20), k6_result(10), k6_result(22)],
            ("go", "health"): [k6_result(9000)],
            ("node", "health"): [k6_result(7001)],
        }
        self.assertEqual(report.target_rates(runs), {"hash": 10, "health": 3500})

    def test_rate_is_at_least_one(self):
        self.assertEqual(report.target_rates({("go", "hash"): [k6_result(1.2)]}), {"hash": 1})


class Build(unittest.TestCase):
    def test_writes_csv_markdown_and_charts(self):
        with tempfile.TemporaryDirectory() as d:
            run = pathlib.Path(d)
            raw = run / "raw"
            raw.mkdir()
            (run / "host.json").write_text(json.dumps({
                "runId": "test", "profile": "laptop", "cpu": "Test CPU", "logicalCpus": 4, "memoryGiB": 8,
                "kernel": "6", "docker": "29", "k6Cpus": 2, "date": "2026-11-06",
                "cpusets": {"api": "0,2", "postgres": "1,3", "k6": "1,3"},
                "durations": {"throughput": "20s", "latency": "30s", "warmup": "10s", "repeats": 3},
                "images": {"go": {"sizeMB": 40.7}, "node": {"sizeMB": 395}}}))
            for api, rps in [("go", 30), ("node", 20)]:
                for i in (1, 2, 3):
                    (raw / f"{api}-hash-throughput-{i}.json").write_text(json.dumps(k6_result(rps + i)))
                (raw / f"{api}-hash-latency.json").write_text(json.dumps(k6_result(10, p99=80, dropped=0)))
                (raw / f"{api}-hash-throughput.stats").write_text(
                    f"1,polyglot-api-bench-{api}-1,190.0%,40MiB\n2,pab-k6,195.0%,90MiB\n")
                (raw / f"{api}-startup-throughput.json").write_text(
                    json.dumps({"api": api, "phase": "throughput", "startupMs": 900, "idleMem": "17MiB"}))

            report.build(run)

            csv_lines = (run / "summary.csv").read_text().splitlines()
            self.assertTrue(csv_lines[0].startswith("endpoint,api,"))
            self.assertEqual(len(csv_lines), 3)
            md = (run / "RESULTS.md").read_text()
            self.assertIn("Test CPU", md)
            self.assertIn("load-generator bound", md)  # k6 at 195% of 2 CPUs > 90%
            svg = (run / "charts" / "hash.svg").read_text()
            self.assertTrue(svg.startswith("<svg"))
            self.assertIn("prefers-color-scheme: dark", svg)


if __name__ == "__main__":
    unittest.main()
