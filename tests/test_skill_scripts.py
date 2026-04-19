from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class HydrolixQueryDebuggingScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.classify_error = load_module(
            "classify_error",
            ROOT / "skills/hydrolix-query-debugging/scripts/classify_error.py",
        )
        cls.summarize_query_stats = load_module(
            "summarize_query_stats",
            ROOT / "skills/hydrolix-query-debugging/scripts/summarize_query_stats.py",
        )

    def test_classifies_timerange_required_error(self) -> None:
        result = self.classify_error.classify(
            "HdxStorageError: hdx_query_timerange_required is set to true"
        )

        self.assertTrue(result["matched"])
        self.assertEqual(result["category"], "timerange_required")
        self.assertIn("timestamp predicate", result["first_action"])

    def test_classifies_memory_limit_error(self) -> None:
        result = self.classify_error.classify(
            "Code: 241. DB::Exception: Memory limit (for query) exceeded"
        )

        self.assertTrue(result["matched"])
        self.assertEqual(result["category"], "memory_limit")
        self.assertEqual(result["reference_file"], "references/circuit-breakers.md")

    def test_summarizes_query_stats_header_json(self) -> None:
        payload = {
            "exec_time_ms": 1500,
            "rows_read": 120000,
            "bytes_read": 4096,
            "num_partitions": 1201,
            "num_peers": 3,
            "peak_memory_usage": 8192,
            "query_attempts": 1,
            "limit_optimization": True,
            "query_detail_runtime_stats": {
                "cached_read_bytes": 1024,
                "net_read_bytes": 4096,
                "hdx_blocks_read": 12,
                "hdx_blocks_skipped": 3,
            },
            "index_stats": {
                "columns_read": ["timestamp", "request_path"],
                "indexes_used": ["timestamp"],
            },
        }

        stats = self.summarize_query_stats.parse_stats(
            "X-HDX-Query-Stats: " + json.dumps(payload)
        )
        summary = self.summarize_query_stats.summarize(stats)

        self.assertEqual(summary["basic"]["rows_read"], 120000)
        self.assertEqual(summary["runtime"]["cached_bytes"], 1024)
        self.assertEqual(summary["runtime"]["net_bytes"], 4096)
        self.assertIn(
            "High partition count; check primary timestamp pruning.",
            summary["warnings"],
        )
        self.assertIn(
            "Network reads exceed cache reads; cache miss may dominate.",
            summary["warnings"],
        )


class BotInsightsScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compare_delta = load_module(
            "compare_delta",
            ROOT / "skills/bot-insights/scripts/compare_delta.py",
        )
        cls.compare_posture = load_module(
            "compare_posture",
            ROOT / "skills/bot-insights/scripts/compare_posture.py",
        )

    def test_compares_current_baseline_objects(self) -> None:
        result = self.compare_delta.compare(
            {
                "current": {"requests": 150, "rate_429_pct": 3.5},
                "baseline": {"requests": 100, "rate_429_pct": 2.0},
            }
        )

        by_metric = {row["metric"]: row for row in result}
        self.assertEqual(by_metric["requests"]["absolute_delta"], 50)
        self.assertEqual(by_metric["requests"]["pct_change"], 50)
        self.assertEqual(by_metric["rate_429_pct"]["absolute_delta"], 1.5)

    def test_compares_period_rows_with_zero_baseline_guard(self) -> None:
        result = self.compare_delta.compare(
            [
                {"period": "current", "requests": 5},
                {"period": "baseline", "requests": 0},
            ]
        )

        self.assertEqual(result[0]["absolute_delta"], 5)
        self.assertEqual(result[0]["pct_change"], 500)

    def test_posture_movement_packet_from_mcp_rows(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "month_over_month",
                "granularity": "day",
                "table_used": "bot_summary_day",
                "scope": {"request_host": "www.example.com"},
                "columns": ["period", "requests", "bot_share_pct"],
                "rows": [
                    ["current", 1500, 30.0],
                    ["baseline", 1000, 20.0],
                ],
            }
        )

        self.assertEqual(result["schema_version"], "bot_posture_movement.v1")
        self.assertEqual(result["comparison_type"], "month_over_month")
        by_metric = {row["name"]: row for row in result["metrics"]}
        self.assertEqual(by_metric["requests"]["absolute_delta"], 500)
        self.assertEqual(by_metric["requests"]["pct_change"], 50)
        self.assertEqual(by_metric["bot_share_pct"]["direction"], "increase")

    def test_posture_zero_baseline_guard(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "previous_window",
                "granularity": "hour",
                "table_used": "bot_summary_hour",
                "current": {"requests": 5},
                "baseline": {"requests": 0},
            }
        )

        metric = result["metrics"][0]
        self.assertEqual(metric["pct_change"], 500)
        self.assertIn("zero_baseline_guard", metric["confidence_reasons"])

    def test_posture_low_count_confidence(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "week_over_week",
                "granularity": "day",
                "table_used": "bot_summary_day",
                "current": {"requests": 50},
                "baseline": {"requests": 40},
            }
        )

        metric = result["metrics"][0]
        self.assertEqual(metric["confidence"], "low")
        self.assertIn("sparse_counts", metric["confidence_reasons"])

    def test_mover_contribution_percentage(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "month_over_month",
                "granularity": "day",
                "table_used": "bot_summary_day",
                "dimension": "client_asn",
                "metric": "requests",
                "total_delta": 100,
                "movers": [
                    {"value": "64500", "current": 180, "baseline": 100},
                    {"value": "64501", "current": 120, "baseline": 100},
                ],
            },
            schema="movers",
        )

        self.assertEqual(result["schema_version"], "bot_mover_attribution.v1")
        self.assertEqual(result["movers"][0]["absolute_delta"], 80)
        self.assertEqual(result["movers"][0]["contribution_pct"], 80)

    def test_control_review_status(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "granularity": "day",
                "table_used": "bot_siem_summary_day",
                "change_time": "2026-04-01T00:00:00Z",
                "target": {"policy_id": "policy-123"},
                "before": {"siem_blocked_requests": 90},
                "after": {"siem_blocked_requests": 130},
                "expected": {"siem_blocked_requests": 100},
                "target_metrics": ["siem_blocked_requests"],
            }
        )

        effect = result["target_effects"][0]
        self.assertEqual(result["schema_version"], "bot_control_review.v1")
        self.assertEqual(effect["absolute_delta_vs_expected"], 30)
        self.assertEqual(effect["status"], "increased")

    def test_interpretation_constraints_included(self) -> None:
        result = self.compare_posture.compare(
            {
                "current": {"requests": 150},
                "baseline": {"requests": 100},
                "table_used": "bot_summary_hour",
            }
        )

        self.assertIn("interpretation_constraints", result)
        self.assertIn(
            "llm_may_summarize_structured_evidence_only",
            result["interpretation_constraints"],
        )


if __name__ == "__main__":
    unittest.main()
