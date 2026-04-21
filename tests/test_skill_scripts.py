from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


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
        cls.scorecard = load_module(
            "scorecard",
            ROOT / "skills/bot-insights/scripts/scorecard.py",
        )
        cls.render_report = load_module(
            "render_report",
            ROOT / "skills/bot-insights/scripts/render_report.py",
        )

    def render_args(self, **overrides):
        defaults = {
            "text": [],
            "file": None,
            "format": "markdown",
            "report_type": None,
            "output": None,
            "limit": None,
            "allow_unknown": False,
            "title": None,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

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

    def test_mover_artifact_preserves_compatibility_metadata(self) -> None:
        payload = {
            "comparison_type": "month_over_month",
            "granularity": "day",
            "table_used": "bot_summary_day",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "current": {"requests": 150},
            "baseline": {"requests": 100},
            "dimension": "client_asn",
            "metric": "requests",
            "movers": [{"value": "64500", "current": 180, "baseline": 100}],
        }

        posture = self.compare_posture.compare(payload, schema="posture")
        mover = self.compare_posture.compare(payload, schema="movers")
        output, warnings = self.render_report.render(
            [posture, mover],
            self.render_args(report_type="executive_posture"),
        )

        self.assertEqual(mover["scope"], payload["scope"])
        self.assertEqual(mover["current_window"], payload["current_window"])
        self.assertEqual(mover["baseline_windows"], payload["baseline_windows"])
        self.assertIn("## Movers", output)
        self.assertFalse(
            any("Omitting optional mover" in warning for warning in warnings),
            f"unexpected mover compatibility warning: {warnings}",
        )

    def test_control_review_status(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "granularity": "day",
                "table_used": "bot_siem_summary_day",
                "change_time": "2026-04-01T00:00:00Z",
                "target": {"policy_id": "policy-123"},
                "scope": {"request_host": "www.example.com"},
                "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
                "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
                "expected_window": {"start": "2026-03-25", "end": "2026-04-01"},
                "before": {"siem_blocked_requests": 90},
                "after": {"siem_blocked_requests": 130},
                "expected": {"siem_blocked_requests": 100},
                "target_metrics": ["siem_blocked_requests"],
            }
        )

        effect = result["target_effects"][0]
        self.assertEqual(result["schema_version"], "bot_control_review.v1")
        self.assertEqual(result["scope"]["request_host"], "www.example.com")
        self.assertEqual(result["expected_basis"], "explicit_target")
        self.assertEqual(result["before_window"]["start"], "2026-03-25")
        self.assertEqual(result["after_window"]["end"], "2026-04-08")
        self.assertEqual(result["expected_window"]["start"], "2026-03-25")
        self.assertEqual(effect["absolute_delta_vs_expected"], 30)
        self.assertEqual(effect["status"], "increased")

    def test_control_review_expected_basis_before_window_when_fallback(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "change_time": "2026-04-01T00:00:00Z",
                "target": {"policy_id": "policy-123"},
                "scope": {"request_host": "www.example.com"},
                "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
                "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
                "before": {"siem_blocked_requests": 100},
                "after": {"siem_blocked_requests": 130},
                "target_metrics": ["siem_blocked_requests"],
            }
        )

        self.assertEqual(result["expected_basis"], "before_window")
        self.assertEqual(result["before_window"]["start"], "2026-03-25")
        self.assertEqual(result["after_window"]["end"], "2026-04-08")
        self.assertEqual(result["expected_window"], result["before_window"])
        self.assertEqual(result["scope"]["request_host"], "www.example.com")

    def test_control_review_expected_basis_explicit_target(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "change_time": "2026-04-01T00:00:00Z",
                "target": {"policy_id": "policy-123"},
                "before": {"siem_blocked_requests": 100},
                "after": {"siem_blocked_requests": 130},
                "expected": {"siem_blocked_requests": 100},
                "target_metrics": ["siem_blocked_requests"],
            }
        )

        self.assertEqual(result["expected_basis"], "explicit_target")

    def test_control_review_expected_basis_external_model_preserved(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "change_time": "2026-04-01T00:00:00Z",
                "target": {"policy_id": "policy-123"},
                "expected_basis": "external_model",
                "before": {"siem_blocked_requests": 100},
                "after": {"siem_blocked_requests": 130},
                "expected": {"siem_blocked_requests": 120},
                "target_metrics": ["siem_blocked_requests"],
            }
        )

        self.assertEqual(result["expected_basis"], "external_model")

    def test_control_review_expected_basis_unknown_when_undeterminable(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "change_time": "2026-04-01T00:00:00Z",
                "target": {"policy_id": "policy-123"},
                "after": {"siem_blocked_requests": 130},
                "expected": {"siem_blocked_requests": 120},
                "target_metrics": ["siem_blocked_requests"],
                "expected_basis": "not_a_real_value",
            }
        )

        self.assertEqual(result["expected_basis"], "explicit_target")

    def test_control_review_expected_basis_unknown_when_no_before_no_expected(
        self,
    ) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "change_time": "2026-04-01T00:00:00Z",
                "target": {"policy_id": "policy-123"},
                "after": {"siem_blocked_requests": 130},
                "target_metrics": ["siem_blocked_requests"],
            }
        )

        self.assertEqual(result["expected_basis"], "unknown")

    def test_control_review_expected_basis_explicit_override_preserved(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "change_time": "2026-04-01T00:00:00Z",
                "target": {"policy_id": "policy-123"},
                "expected_basis": "unknown",
                "before": {"siem_blocked_requests": 100},
                "after": {"siem_blocked_requests": 130},
                "expected": {"siem_blocked_requests": 120},
                "target_metrics": ["siem_blocked_requests"],
            }
        )

        self.assertEqual(result["expected_basis"], "unknown")

    def test_control_review_does_not_infer_windows_from_change_time(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "change_time": "2026-04-01T00:00:00Z",
                "target": {"policy_id": "policy-123"},
                "before": {"siem_blocked_requests": 100},
                "after": {"siem_blocked_requests": 130},
                "target_metrics": ["siem_blocked_requests"],
            }
        )

        self.assertNotIn("before_window", result)
        self.assertNotIn("after_window", result)
        self.assertNotIn("expected_window", result)

    def test_control_review_preserves_expected_window_when_supplied(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "change_time": "2026-04-01T00:00:00Z",
                "target": {"policy_id": "policy-123"},
                "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
                "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
                "expected_window": {
                    "start": "2026-03-18",
                    "end": "2026-03-25",
                    "label": "two_weeks_prior",
                },
                "before": {"siem_blocked_requests": 100},
                "after": {"siem_blocked_requests": 130},
                "expected": {"siem_blocked_requests": 90},
                "target_metrics": ["siem_blocked_requests"],
            }
        )

        self.assertEqual(result["expected_window"]["label"], "two_weeks_prior")
        self.assertEqual(result["expected_basis"], "explicit_target")

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

    def test_scorecard_basic_entity_from_object_rows(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "client_asn",
                "comparison_type": "week_over_week",
                "granularity": "hour",
                "table_used": "bot_summary_hour",
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 1500,
                        "baseline_requests": 500,
                        "current_bot_share_pct": 80,
                        "baseline_bot_share_pct": 40,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertEqual(card["schema_version"], "bot_entity_scorecard.v1")
        self.assertEqual(card["entity_type"], "client_asn")
        self.assertEqual(card["entity"], "64500")
        self.assertGreater(card["score"], 0)
        self.assertIn("movement", card["domain_scores"])

    def test_scorecard_mcp_columns_rows_conversion(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "comparison_type": "month_over_month",
                "granularity": "day",
                "table_used": "bot_summary_day",
                "columns": [
                    "request_host",
                    "current_requests",
                    "baseline_requests",
                    "bad_bot_share_pct",
                ],
                "rows": [
                    ["www.example.com", 1000, 900, 60],
                    ["api.example.com", 800, 700, 10],
                ],
            }
        )

        entities = {card["entity"] for card in result["scorecards"]}
        self.assertEqual(entities, {"www.example.com", "api.example.com"})
        self.assertEqual(result["index"]["schema_version"], "bot_scorecard_index.v1")

    def test_scorecard_high_cache_busting_score(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_path_norm",
                "table_used": "bot_agg_path_hour",
                "rows": [
                    {
                        "request_path_norm": "/api/search",
                        "current_requests": 5000,
                        "baseline_requests": 1000,
                        "qs_diversity_ratio": 0.93,
                        "current_cache_miss_pct": 88,
                        "baseline_cache_miss_pct": 20,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        feature_names = {feature["name"] for feature in card["features"]}
        self.assertIn("querystring_diversity_high", feature_names)
        self.assertIn("querystring_diversity_with_high_miss_rate", feature_names)
        self.assertGreaterEqual(card["domain_scores"]["cache_busting"], 40)

    def test_scorecard_high_origin_impact_score(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_path_norm",
                "table_used": "bot_agg_path_hour",
                "rows": [
                    {
                        "request_path_norm": "/checkout",
                        "current_requests": 4000,
                        "baseline_requests": 3000,
                        "origin_cost_contribution_pct": 45,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        feature_names = {feature["name"] for feature in card["features"]}
        self.assertIn("origin_cost_contribution_high", feature_names)
        self.assertEqual(card["domain_scores"]["origin_impact"], 18)

    def test_scorecard_new_entity_zero_baseline_guard(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "client_asn",
                "table_used": "bot_summary_hour",
                "rows": [
                    {
                        "client_asn": "64501",
                        "current_requests": 250,
                        "baseline_requests": 0,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        feature_names = {feature["name"] for feature in card["features"]}
        self.assertIn("new_entity", feature_names)
        self.assertIn("zero_baseline_guard", card["confidence_reasons"])

    def test_scorecard_does_not_synthesize_contribution_for_limited_rowset(
        self,
    ) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "client_asn",
                "table_used": "bot_summary_hour",
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 5000,
                        "baseline_requests": 1000,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        feature_names = {feature["name"] for feature in card["features"]}
        missing = {
            feature["name"]: feature["missing_inputs"]
            for feature in card["not_evaluated_features"]
        }
        self.assertNotIn("contribution_to_total_delta_high", feature_names)
        self.assertEqual(
            missing["contribution_to_total_delta_high"], ["contribution_pct"]
        )

    def test_scorecard_synthesizes_contribution_for_explicit_complete_rowset(
        self,
    ) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "client_asn",
                "table_used": "bot_summary_hour",
                "rowset_complete": True,
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 5000,
                        "baseline_requests": 1000,
                    },
                    {
                        "client_asn": "64501",
                        "current_requests": 2000,
                        "baseline_requests": 1000,
                    },
                ],
            }
        )

        by_entity = {card["entity"]: card for card in result["scorecards"]}
        features = {
            feature["name"]: feature for feature in by_entity["64500"]["features"]
        }
        self.assertEqual(features["contribution_to_total_delta_high"]["current"], 80)

    def test_scorecard_sparse_counts_lower_confidence(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "bot_class",
                "table_used": "bot_summary_hour",
                "rows": [
                    {
                        "bot_class": "bad",
                        "current_requests": 10,
                        "baseline_requests": 5,
                        "bad_bot_share_pct": 100,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertEqual(card["confidence"], "low")
        self.assertIn("sparse_counts", card["confidence_reasons"])

    def test_scorecard_prefixed_siem_inputs_count_as_available(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "bot_siem_summary_hour",
                "rows": [
                    {
                        "request_host": "www.example.com",
                        "current_requests": 1000,
                        "baseline_requests": 900,
                        "current_siem_blocked_requests": 25,
                        "current_siem_auth_fail_requests": 5,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        feature_names = {feature["name"] for feature in card["features"]}
        self.assertIn("siem_blocked_present", feature_names)
        self.assertIn("siem_auth_fail_present", feature_names)
        self.assertNotIn("siem_unavailable", card["confidence_reasons"])

    def test_scorecard_absent_siem_inputs_mark_unavailable(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "bot_summary_day",
                "rows": [
                    {
                        "request_host": "www.example.com",
                        "current_requests": 1000,
                        "baseline_requests": 900,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertIn("siem_unavailable", card["confidence_reasons"])

    def test_scorecard_missing_feature_inputs_are_not_evaluated(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "bot_summary_day",
                "rows": [
                    {
                        "request_host": "www.example.com",
                        "current_requests": 1000,
                        "baseline_requests": 900,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        missing = {feature["name"] for feature in card["not_evaluated_features"]}
        self.assertIn("querystring_diversity_high", missing)
        self.assertIn("feature_input_missing", card["confidence_reasons"])

    def test_scorecard_index_ranks_entities_by_score(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_path_norm",
                "table_used": "bot_agg_path_hour",
                "rows": [
                    {
                        "request_path_norm": "/low",
                        "current_requests": 1000,
                        "baseline_requests": 900,
                    },
                    {
                        "request_path_norm": "/high",
                        "current_requests": 6000,
                        "baseline_requests": 500,
                        "qs_diversity_ratio": 0.95,
                        "current_cache_miss_pct": 90,
                        "baseline_cache_miss_pct": 10,
                        "bad_bot_share_pct": 90,
                    },
                ],
            }
        )

        ranked = result["index"]["ranked_entities"]
        self.assertEqual(ranked[0]["entity"], "/high")
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])

    def test_scorecard_limit_metadata_when_truncated(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "bot_summary_hour",
                "rows": [
                    {
                        "request_host": "a.example.com",
                        "current_requests": 5000,
                        "baseline_requests": 100,
                    },
                    {
                        "request_host": "b.example.com",
                        "current_requests": 4000,
                        "baseline_requests": 100,
                    },
                    {
                        "request_host": "c.example.com",
                        "current_requests": 3000,
                        "baseline_requests": 100,
                    },
                ],
            },
            limit=2,
        )

        self.assertEqual(result["producer_limit"], 2)
        self.assertEqual(result["result_row_count"], 2)
        self.assertTrue(result["result_truncated"])
        self.assertEqual(result["total_ranked_entities"], 3)
        self.assertEqual(result["index"]["producer_limit"], 2)
        self.assertTrue(result["index"]["result_truncated"])

    def test_scorecard_interpretation_constraints_always_included(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "ai_category",
                "table_used": "bot_summary_day",
                "rows": [
                    {
                        "ai_category": "crawler",
                        "current_requests": 1000,
                        "baseline_requests": 100,
                        "current_ai_crawler_requests": 1000,
                        "baseline_ai_crawler_requests": 100,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertIn("interpretation_constraints", card)
        self.assertIn("interpretation_constraints", result["index"])
        self.assertIn("rule_based_scorecard", card["interpretation_constraints"])

    def test_scorecard_includes_window_metadata(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "client_asn",
                "comparison_type": "week_over_week",
                "table_used": "bot_summary_hour",
                "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
                "baseline_windows": [
                    {
                        "start": "2026-03-25",
                        "end": "2026-04-01",
                        "label": "previous_week",
                    }
                ],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 1500,
                        "baseline_requests": 500,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertEqual(card["current_window"]["start"], "2026-04-01")
        self.assertEqual(card["baseline_windows"][0]["label"], "previous_week")
        self.assertEqual(result["index"]["current_window"]["end"], "2026-04-08")
        self.assertEqual(result["index"]["baseline_windows"][0]["start"], "2026-03-25")

    def test_scorecard_rejects_mixed_period_and_combined_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not mix period-split rows"):
            self.scorecard.build_artifacts(
                {
                    "entity_type": "client_asn",
                    "table_used": "bot_summary_hour",
                    "rows": [
                        {"period": "current", "client_asn": "64500", "requests": 1000},
                        {"period": "baseline", "client_asn": "64500", "requests": 500},
                        {
                            "client_asn": "64501",
                            "current_requests": 1200,
                            "baseline_requests": 600,
                        },
                    ],
                }
            )

    def test_scorecard_preserves_payload_level_provenance(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "bot_class",
                "table_used": "bot_summary_day",
                "rowset_scope": {
                    "population": "good_bot",
                    "filters": {"bot_class": "good_bot"},
                    "entity_type": "bot_class",
                    "table_used": "bot_summary_day",
                },
                "feature_provenance": {
                    "rate_429_delta_high": {
                        "rowset_scope": {"population": "good_bot"},
                        "metric_inputs": [
                            "current_rate_429_pct",
                            "baseline_rate_429_pct",
                        ],
                    }
                },
                "rows": [
                    {
                        "bot_class": "good_bot",
                        "current_requests": 5000,
                        "baseline_requests": 1000,
                        "current_rate_429_pct": 12,
                        "baseline_rate_429_pct": 1,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertEqual(card["rowset_scope"]["population"], "good_bot")
        self.assertEqual(
            card["feature_provenance"]["rate_429_delta_high"]["metric_inputs"],
            ["current_rate_429_pct", "baseline_rate_429_pct"],
        )

    def test_scorecard_row_level_provenance_overrides_payload(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "client_asn",
                "table_used": "bot_summary_hour",
                "rowset_scope": {"population": "all_traffic"},
                "feature_provenance": {
                    "rate_429_delta_high": {
                        "rowset_scope": {"population": "all_traffic"},
                    }
                },
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 2000,
                        "baseline_requests": 500,
                        "rowset_scope": {"population": "crawler"},
                        "feature_provenance": {
                            "rate_429_delta_high": {
                                "rowset_scope": {"population": "crawler"},
                                "metric_inputs": ["current_rate_429_pct"],
                            }
                        },
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertEqual(card["rowset_scope"]["population"], "crawler")
        self.assertEqual(
            card["feature_provenance"]["rate_429_delta_high"]["rowset_scope"][
                "population"
            ],
            "crawler",
        )

    def test_scorecard_period_split_rows_preserve_matching_provenance(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "client_asn",
                "table_used": "bot_summary_hour",
                "rows": [
                    {
                        "period": "current",
                        "client_asn": "64500",
                        "requests": 2000,
                        "rate_429_pct": 12,
                        "rowset_scope": {"population": "crawler"},
                        "feature_provenance": {
                            "rate_429_delta_high": {
                                "rowset_scope": {"population": "crawler"},
                                "metric_inputs": [
                                    "current_rate_429_pct",
                                    "baseline_rate_429_pct",
                                ],
                            }
                        },
                    },
                    {
                        "period": "baseline",
                        "client_asn": "64500",
                        "requests": 500,
                        "rate_429_pct": 1,
                        "rowset_scope": {"population": "crawler"},
                        "feature_provenance": {
                            "rate_429_delta_high": {
                                "rowset_scope": {"population": "crawler"},
                                "metric_inputs": [
                                    "current_rate_429_pct",
                                    "baseline_rate_429_pct",
                                ],
                            }
                        },
                    },
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertEqual(card["rowset_scope"]["population"], "crawler")
        self.assertEqual(
            card["feature_provenance"]["rate_429_delta_high"]["metric_inputs"],
            ["current_rate_429_pct", "baseline_rate_429_pct"],
        )

    def test_scorecard_period_split_rows_reject_conflicting_rowset_scope(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError, "must not disagree on rowset_scope"
        ):
            self.scorecard.build_artifacts(
                {
                    "entity_type": "client_asn",
                    "table_used": "bot_summary_hour",
                    "rows": [
                        {
                            "period": "current",
                            "client_asn": "64500",
                            "requests": 2000,
                            "rowset_scope": {"population": "crawler"},
                        },
                        {
                            "period": "baseline",
                            "client_asn": "64500",
                            "requests": 500,
                            "rowset_scope": {"population": "all_traffic"},
                        },
                    ],
                }
            )

    def test_scorecard_period_split_rows_reject_conflicting_feature_provenance(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError, "must not disagree on feature_provenance"
        ):
            self.scorecard.build_artifacts(
                {
                    "entity_type": "client_asn",
                    "table_used": "bot_summary_hour",
                    "rows": [
                        {
                            "period": "current",
                            "client_asn": "64500",
                            "requests": 2000,
                            "rate_429_pct": 12,
                            "feature_provenance": {
                                "rate_429_delta_high": {
                                    "rowset_scope": {"population": "crawler"}
                                }
                            },
                        },
                        {
                            "period": "baseline",
                            "client_asn": "64500",
                            "requests": 500,
                            "rate_429_pct": 1,
                            "feature_provenance": {
                                "rate_429_delta_high": {
                                    "rowset_scope": {"population": "all_traffic"}
                                }
                            },
                        },
                    ],
                }
            )

    def test_scorecard_rejects_invalid_rowset_scope_population(self) -> None:
        with self.assertRaisesRegex(ValueError, "population must be one of"):
            self.scorecard.build_artifacts(
                {
                    "entity_type": "client_asn",
                    "table_used": "bot_summary_hour",
                    "rowset_scope": {"population": "mystery"},
                    "rows": [
                        {
                            "client_asn": "64500",
                            "current_requests": 1500,
                            "baseline_requests": 500,
                        }
                    ],
                }
            )

    def test_scorecard_rejects_non_object_feature_provenance(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "feature_provenance must be a JSON object"
        ):
            self.scorecard.build_artifacts(
                {
                    "entity_type": "client_asn",
                    "table_used": "bot_summary_hour",
                    "feature_provenance": ["rate_429_delta_high"],
                    "rows": [
                        {
                            "client_asn": "64500",
                            "current_requests": 1500,
                            "baseline_requests": 500,
                        }
                    ],
                }
            )

    def test_scorecard_rejects_non_string_metric_inputs(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "metric_inputs must be an array of strings"
        ):
            self.scorecard.build_artifacts(
                {
                    "entity_type": "client_asn",
                    "table_used": "bot_summary_hour",
                    "feature_provenance": {
                        "rate_429_delta_high": {"metric_inputs": ["ok", 42]}
                    },
                    "rows": [
                        {
                            "client_asn": "64500",
                            "current_requests": 1500,
                            "baseline_requests": 500,
                        }
                    ],
                }
            )

    def test_scorecard_rejects_row_level_invalid_provenance(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "row.rowset_scope.population must be one of"
        ):
            self.scorecard.build_artifacts(
                {
                    "entity_type": "client_asn",
                    "table_used": "bot_summary_hour",
                    "rows": [
                        {
                            "client_asn": "64500",
                            "current_requests": 1500,
                            "baseline_requests": 500,
                            "rowset_scope": {"population": "bots"},
                        }
                    ],
                }
            )

    def test_scorecard_default_output_carries_packet_limit_metadata(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "bot_summary_hour",
                "rows": [
                    {
                        "request_host": "a.example.com",
                        "current_requests": 5000,
                        "baseline_requests": 100,
                    },
                    {
                        "request_host": "b.example.com",
                        "current_requests": 4000,
                        "baseline_requests": 100,
                    },
                    {
                        "request_host": "c.example.com",
                        "current_requests": 3000,
                        "baseline_requests": 100,
                    },
                ],
            },
            limit=2,
        )

        self.assertEqual(result["schema_version"], "bot_scorecard_artifacts.v1")
        self.assertEqual(result["producer_limit"], 2)
        self.assertEqual(result["result_row_count"], 2)
        self.assertTrue(result["result_truncated"])
        self.assertEqual(result["total_ranked_entities"], 3)

    def test_scorecard_index_output_carries_packet_limit_metadata(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "bot_summary_hour",
                "rows": [
                    {
                        "request_host": "a.example.com",
                        "current_requests": 5000,
                        "baseline_requests": 100,
                    },
                    {
                        "request_host": "b.example.com",
                        "current_requests": 4000,
                        "baseline_requests": 100,
                    },
                    {
                        "request_host": "c.example.com",
                        "current_requests": 3000,
                        "baseline_requests": 100,
                    },
                ],
            },
            limit=2,
        )

        index = result["index"]
        self.assertEqual(index["schema_version"], "bot_scorecard_index.v1")
        self.assertEqual(index["producer_limit"], 2)
        self.assertEqual(index["result_row_count"], 2)
        self.assertTrue(index["result_truncated"])
        self.assertEqual(index["total_ranked_entities"], 3)

    def test_scorecard_bare_scorecards_output_is_plain_list(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "bot_summary_hour",
                "rows": [
                    {
                        "request_host": "a.example.com",
                        "current_requests": 5000,
                        "baseline_requests": 100,
                    },
                    {
                        "request_host": "b.example.com",
                        "current_requests": 4000,
                        "baseline_requests": 100,
                    },
                    {
                        "request_host": "c.example.com",
                        "current_requests": 3000,
                        "baseline_requests": 100,
                    },
                ],
            },
            limit=2,
        )

        bare_scorecards = result["scorecards"]
        self.assertIsInstance(bare_scorecards, list)
        self.assertEqual(len(bare_scorecards), 2)
        for card in bare_scorecards:
            self.assertNotIn("producer_limit", card)
            self.assertNotIn("result_row_count", card)
            self.assertNotIn("result_truncated", card)
            self.assertNotIn("total_ranked_entities", card)

    def test_render_report_wrapper_scorecard_packet_and_child_citation(self) -> None:
        artifacts = self.scorecard.build_artifacts(
            {
                "entity_type": "request_path_norm",
                "table_used": "bot_agg_path_hour",
                "scope": {"request_host": "www.example.com"},
                "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
                "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
                "rows": [
                    {
                        "request_path_norm": "/api/search",
                        "current_requests": 5000,
                        "baseline_requests": 1000,
                        "qs_diversity_ratio": 0.93,
                        "current_cache_miss_pct": 88,
                        "baseline_cache_miss_pct": 20,
                    }
                ],
            }
        )
        artifacts["artifact_id"] = "scorecard-pack"
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "soc_triage",
            "title": "SOC <Report>",
            "artifacts": [artifacts],
            "analyst_notes": [
                {
                    "note_id": "note-1",
                    "author_type": "llm",
                    "text": "Review <this> path.",
                    "data_sources": [
                        {
                            "artifact_id": "scorecard-pack#scorecard-1",
                            "json_pointer": "/features/0/name",
                            "label": "first feature",
                        }
                    ],
                }
            ],
        }

        output, warnings = self.render_report.render(
            wrapper, self.render_args(format="html")
        )

        self.assertIn("SOC &lt;Report&gt;", output)
        self.assertIn("Review &lt;this&gt; path.", output)
        self.assertIn("scorecard-pack#scorecard-1", output)
        self.assertIn("<svg", output)
        self.assertEqual(warnings, [])

    def test_render_report_raw_array_requires_report_type(self) -> None:
        with self.assertRaisesRegex(
            self.render_report.ReportError, "requires --report-type"
        ):
            self.render_report.render(
                [{"schema_version": "bot_scorecard_index.v1", "ranked_entities": []}],
                self.render_args(),
            )

    def test_render_report_conflicting_report_type_fails(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "metrics": [],
                }
            ],
        }

        with self.assertRaisesRegex(self.render_report.ReportError, "conflicts"):
            self.render_report.render(
                wrapper,
                self.render_args(report_type="soc_triage"),
            )

    def test_render_report_wrapper_report_type_must_be_string(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": ["executive_posture"],
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "metrics": [],
                }
            ],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "report_type must be a string"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_timeseries_rejected_even_with_allow_unknown(self) -> None:
        with self.assertRaisesRegex(self.render_report.ReportError, "unsupported"):
            self.render_report.render(
                {"schema_version": "bot_timeseries.v1", "series": []},
                self.render_args(report_type="executive_posture", allow_unknown=True),
            )

    def test_render_report_soc_index_only_degraded(self) -> None:
        output, warnings = self.render_report.render(
            {
                "schema_version": "bot_scorecard_index.v1",
                "ranked_entities": [
                    {
                        "rank": 1,
                        "entity_type": "client_asn",
                        "entity": "64500",
                        "score": 80,
                        "band": "urgent_review",
                        "primary_domain": "security_evidence",
                        "confidence": "medium",
                    }
                ],
            },
            self.render_args(report_type="soc_triage"),
        )

        self.assertIn("Top Risky Entities", output)
        self.assertNotIn("Domain Score Matrix", output)
        self.assertTrue(any("degraded ranking-only" in warning for warning in warnings))

    def test_render_report_rejects_malformed_scorecard_packet_children(self) -> None:
        packet = {
            "schema_version": "bot_scorecard_artifacts.v1",
            "index": {
                "schema_version": "unexpected_index.v1",
                "ranked_entities": [{"entity_type": "client_asn", "entity": "64500"}],
            },
            "scorecards": [
                {
                    "schema_version": "unexpected_scorecard.v1",
                    "entity_type": "client_asn",
                    "entity": "64500",
                    "score": 80,
                }
            ],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "requires bot_entity_scorecard"
        ):
            self.render_report.render(
                packet, self.render_args(report_type="crawler_governance")
            )

    def test_render_report_rejects_incompatible_standalone_scorecard_pairing(
        self,
    ) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "scope": {"request_host": "a.example.com"},
            "comparison_type": "previous_window",
            "table_used": "bot_summary_hour",
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "ranked_entities": [
                {"entity_type": "client_asn", "entity": "64500", "score": 80}
            ],
        }
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "entity_type": "client_asn",
            "entity": "64500",
            "scope": {"request_host": "b.example.com"},
            "comparison_type": "previous_window",
            "table_used": "bot_summary_hour",
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "score": 80,
            "band": "urgent_review",
            "domain_scores": {},
            "features": [],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "metadata mismatch"
        ):
            self.render_report.render(
                [index, scorecard], self.render_args(report_type="soc_triage")
            )

    def test_render_report_unresolved_analyst_citation_fails(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [{"schema_version": "bot_posture_movement.v1", "metrics": []}],
            "analyst_notes": [
                {
                    "author_type": "llm",
                    "text": "Review this.",
                    "data_sources": [
                        {
                            "schema_version": "bot_posture_movement.v1",
                            "json_pointer": "/missing",
                        }
                    ],
                }
            ],
        }

        with self.assertRaisesRegex(self.render_report.ReportError, "pointer /missing"):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_malformed_analyst_pointer_fails(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [{"schema_version": "bot_posture_movement.v1", "metrics": []}],
            "analyst_notes": [
                {
                    "author_type": "llm",
                    "text": "Review this.",
                    "data_sources": [
                        {
                            "schema_version": "bot_posture_movement.v1",
                            "json_pointer": "/metrics~2bad",
                        }
                    ],
                }
            ],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "pointer /metrics~2bad"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_negative_analyst_pointer_index_fails(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "posture-1",
                    "metrics": [
                        {"name": "first"},
                        {"name": "last"},
                    ],
                }
            ],
            "analyst_notes": [
                {
                    "author_type": "analyst",
                    "text": "Review this.",
                    "data_sources": [
                        {
                            "artifact_id": "posture-1",
                            "json_pointer": "/metrics/-1/name",
                        }
                    ],
                }
            ],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "pointer /metrics/-1/name"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_crawler_generic_rates_require_structured_provenance(
        self,
    ) -> None:
        base_card = {
            "schema_version": "bot_entity_scorecard.v1",
            "entity_type": "request_host",
            "entity": "www.example.com",
            "score": 20,
            "band": "watch",
            "domain_scores": {"crawler_governance": 12},
            "features": [
                {
                    "domain": "crawler_governance",
                    "name": "rate_429_delta_high",
                    "points": 12,
                    "evidence": "Crawler-like 429 movement mentioned in free-form text.",
                    "supporting_metrics": {"crawler_hint": "good_bot"},
                }
            ],
            "not_evaluated_features": [],
        }

        output, warnings = self.render_report.render(
            base_card,
            self.render_args(report_type="crawler_governance"),
        )
        self.assertIn("No relevant crawler governance evidence available", output)
        self.assertIn("Crawler provenance gaps", output)
        self.assertIn("rate\\_429\\_delta\\_high", output)
        self.assertIn("structured `rowset_scope`/`feature_provenance`", output)
        self.assertTrue(any("no eligible evaluated" in warning for warning in warnings))

        with_provenance = dict(base_card)
        with_provenance["rowset_scope"] = {"population": "good_bot"}
        output, warnings = self.render_report.render(
            with_provenance,
            self.render_args(report_type="crawler_governance"),
        )
        self.assertIn("rate\\_429\\_delta\\_high", output)

    def test_render_report_raw_entity_scorecard_requires_report_type(self) -> None:
        with self.assertRaisesRegex(
            self.render_report.ReportError, "Missing or ambiguous"
        ):
            self.render_report.render(
                {
                    "schema_version": "bot_entity_scorecard.v1",
                    "entity_type": "client_asn",
                    "entity": "64500",
                    "score": 10,
                },
                self.render_args(),
            )

    def test_render_report_default_limits_match_design(self) -> None:
        self.assertEqual(self.render_report.default_limit("soc_triage"), 10)
        self.assertEqual(self.render_report.default_limit("crawler_governance"), 10)
        self.assertEqual(self.render_report.default_limit("edge_ops_impact"), 10)
        self.assertEqual(self.render_report.default_limit("scorecard_brief"), 20)

    def test_render_report_rejects_unsupported_top_level_shape(self) -> None:
        with self.assertRaisesRegex(self.render_report.ReportError, "Input must be"):
            self.render_report.render(
                "not-an-object", self.render_args(report_type="executive_posture")
            )

    def test_render_report_rejects_empty_array(self) -> None:
        with self.assertRaisesRegex(self.render_report.ReportError, "non-empty"):
            self.render_report.render(
                [], self.render_args(report_type="executive_posture")
            )

    def test_render_report_rejects_non_object_artifact_entries(self) -> None:
        with self.assertRaisesRegex(self.render_report.ReportError, "JSON objects"):
            self.render_report.render(
                ["not an artifact"],
                self.render_args(report_type="executive_posture"),
            )

    def test_render_report_rejects_artifact_object_missing_schema(self) -> None:
        with self.assertRaisesRegex(self.render_report.ReportError, "schema_version"):
            self.render_report.render(
                {"metrics": []}, self.render_args(report_type="executive_posture")
            )

    def test_render_report_rejects_unknown_schema_by_default(self) -> None:
        with self.assertRaisesRegex(
            self.render_report.ReportError, "Unknown artifact schema"
        ):
            self.render_report.render(
                {"schema_version": "made_up.v1"},
                self.render_args(report_type="executive_posture"),
            )

    def test_render_report_allow_unknown_skips_with_warning(self) -> None:
        # An unknown artifact alone normalizes to nothing; renderer should fail because
        # no supported artifacts remain, but the skip warning should fire first.
        ctx = self.render_report.ReportContext()
        normalized = self.render_report.normalize_artifacts(
            [{"schema_version": "made_up.v1"}],
            allow_unknown=True,
            ctx=ctx,
        )
        self.assertEqual(normalized, [])
        self.assertTrue(
            any("Skipped unknown artifact schema" in w for w in ctx.warnings)
        )

    def test_render_report_raw_posture_infers_executive(self) -> None:
        output, _ = self.render_report.render(
            {
                "schema_version": "bot_posture_movement.v1",
                "scope": {"request_host": "www.example.com"},
                "metrics": [],
            },
            self.render_args(),
        )
        self.assertIn("Report type: `executive_posture`", output)

    def test_render_report_raw_control_infers_control_review(self) -> None:
        output, _ = self.render_report.render(
            {
                "schema_version": "bot_control_review.v1",
                "target": {"policy_id": "policy-1"},
                "scope": {"request_host": "www.example.com"},
                "target_effects": [],
            },
            self.render_args(),
        )
        self.assertIn("Report type: `control_review`", output)

    def test_render_report_raw_mover_requires_explicit_report_type(self) -> None:
        with self.assertRaisesRegex(
            self.render_report.ReportError, "Missing or ambiguous"
        ):
            self.render_report.render(
                {"schema_version": "bot_mover_attribution.v1", "movers": []},
                self.render_args(),
            )

    def test_render_report_raw_scorecard_packet_requires_report_type(self) -> None:
        packet = {
            "schema_version": "bot_scorecard_artifacts.v1",
            "index": {
                "schema_version": "bot_scorecard_index.v1",
                "ranked_entities": [],
            },
            "scorecards": [],
        }
        with self.assertRaisesRegex(
            self.render_report.ReportError, "Missing or ambiguous"
        ):
            self.render_report.render(packet, self.render_args())

    def test_render_report_cli_title_overrides_wrapper_with_warning(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "title": "Wrapper Title",
            "artifacts": [{"schema_version": "bot_posture_movement.v1", "metrics": []}],
        }
        output, warnings = self.render_report.render(
            wrapper, self.render_args(title="CLI Title")
        )
        self.assertIn("CLI Title", output)
        self.assertNotIn("Wrapper Title", output.splitlines()[0])
        self.assertTrue(any("--title overrides wrapper" in w for w in warnings))

    def test_render_report_cli_limit_overrides_wrapper_with_warning(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "limit": 10,
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "metrics": [
                        {"name": f"m{i}", "current": i, "baseline": 0} for i in range(5)
                    ],
                }
            ],
        }
        _, warnings = self.render_report.render(wrapper, self.render_args(limit=2))
        self.assertTrue(any("--limit overrides wrapper" in w for w in warnings))

    def test_render_report_cli_limit_zero_negative_and_non_int_fail(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [{"schema_version": "bot_posture_movement.v1", "metrics": []}],
        }
        for bad in (0, -1, "10", True):
            with self.assertRaisesRegex(
                self.render_report.ReportError, "positive integer"
            ):
                self.render_report.render(wrapper, self.render_args(limit=bad))

    def test_render_report_wrapper_limit_must_be_positive_integer(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "limit": 0,
            "artifacts": [{"schema_version": "bot_posture_movement.v1", "metrics": []}],
        }
        with self.assertRaisesRegex(self.render_report.ReportError, "Wrapper limit"):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_wrapper_and_cli_matching_report_type_renders(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [{"schema_version": "bot_posture_movement.v1", "metrics": []}],
        }
        output, _ = self.render_report.render(
            wrapper, self.render_args(report_type="executive_posture")
        )
        self.assertIn("Report type: `executive_posture`", output)

    def test_render_report_display_limit_does_not_block_required_validation(
        self,
    ) -> None:
        # SOC triage requires an index; with display limit=1 and a small index, validation
        # must still pass and the report renders.
        artifact = {
            "schema_version": "bot_scorecard_index.v1",
            "scope": {"request_host": "www.example.com"},
            "ranked_entities": [
                {
                    "rank": i,
                    "entity_type": "client_asn",
                    "entity": str(64500 + i),
                    "score": 80 - i,
                }
                for i in range(1, 4)
            ],
        }
        output, _ = self.render_report.render(
            artifact, self.render_args(report_type="soc_triage", limit=1)
        )
        self.assertIn("Top Risky Entities", output)

    def test_render_report_scope_label_wins(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "scope_label": "wrapper-scope-label",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "scope": {"request_host": "different.example.com"},
                    "metrics": [],
                }
            ],
        }
        output, _ = self.render_report.render(wrapper, self.render_args())
        self.assertIn("Scope: wrapper\\-scope\\-label", output)

    def test_render_report_scope_uses_unambiguous_artifact_scope(self) -> None:
        artifact = {
            "schema_version": "bot_posture_movement.v1",
            "scope": {"request_host": "www.example.com"},
            "metrics": [],
        }
        output, warnings = self.render_report.render(artifact, self.render_args())
        self.assertIn("Scope: request\\_host=www\\.example\\.com", output)
        self.assertFalse(any("Scope" in w for w in warnings))

    def test_render_report_scope_unavailable_warns(self) -> None:
        artifact = {"schema_version": "bot_posture_movement.v1", "metrics": []}
        output, warnings = self.render_report.render(artifact, self.render_args())
        self.assertIn("Scope: unavailable", output)
        self.assertTrue(any("Scope unavailable" in w for w in warnings))

    def test_render_report_scope_mixed_warns(self) -> None:
        index_a = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "index-a",
            "scope": {"request_host": "a.example.com"},
            "ranked_entities": [
                {"rank": 1, "entity_type": "client_asn", "entity": "64500", "score": 80}
            ],
        }
        index_b_posture = {
            "schema_version": "bot_posture_movement.v1",
            "artifact_id": "posture-b",
            "scope": {"request_host": "b.example.com"},
            "metrics": [],
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_day",
            "comparison_type": "previous_window",
        }
        output, warnings = self.render_report.render(
            [index_a, index_b_posture], self.render_args(report_type="soc_triage")
        )
        self.assertIn("Scope: request\\_host=a\\.example\\.com", output)
        self.assertTrue(
            any(
                "Omitting optional posture" in w and "conflict on scope" in w
                for w in warnings
            )
        )

    def test_render_report_same_packet_degraded_missing_metadata(self) -> None:
        packet = {
            "schema_version": "bot_scorecard_artifacts.v1",
            "artifact_id": "pack-1",
            "index": {
                "schema_version": "bot_scorecard_index.v1",
                "ranked_entities": [
                    {
                        "rank": 1,
                        "entity_type": "client_asn",
                        "entity": "64500",
                        "score": 80,
                        "band": "urgent_review",
                    }
                ],
            },
            "scorecards": [
                {
                    "schema_version": "bot_entity_scorecard.v1",
                    "entity_type": "client_asn",
                    "entity": "64500",
                    "score": 80,
                    "band": "urgent_review",
                    "domain_scores": {"security_evidence": 80},
                    "features": [
                        {
                            "domain": "security_evidence",
                            "name": "bad_bot_share_high",
                            "points": 80,
                            "evidence": "x",
                        }
                    ],
                }
            ],
        }

        output, warnings = self.render_report.render(
            packet, self.render_args(report_type="soc_triage")
        )

        self.assertIn("Domain Score Matrix", output)
        self.assertTrue(
            any("same-packet" in w for w in warnings),
            f"warnings missing same-packet degradation: {warnings}",
        )

    def test_render_report_standalone_compatible_pairing_renders(self) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx-1",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "comparison_type": "previous_window",
            "ranked_entities": [
                {"rank": 1, "entity_type": "client_asn", "entity": "64500", "score": 80}
            ],
        }
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc-1",
            "entity_type": "client_asn",
            "entity": "64500",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "comparison_type": "previous_window",
            "score": 80,
            "band": "urgent_review",
            "domain_scores": {"security_evidence": 80},
            "features": [
                {
                    "domain": "security_evidence",
                    "name": "bad_bot_share_high",
                    "points": 80,
                    "evidence": "x",
                }
            ],
        }

        output, warnings = self.render_report.render(
            [index, scorecard], self.render_args(report_type="soc_triage")
        )

        self.assertIn("Domain Score Matrix", output)
        self.assertFalse(
            any("metadata mismatch" in w for w in warnings),
            f"unexpected pairing warning: {warnings}",
        )

    def test_render_report_cross_packet_scorecard_pairing_rejected(self) -> None:
        packet_a = {
            "schema_version": "bot_scorecard_artifacts.v1",
            "artifact_id": "pack-a",
            "index": {
                "schema_version": "bot_scorecard_index.v1",
                "scope": {"request_host": "a.example.com"},
                "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
                "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
                "table_used": "bot_summary_hour",
                "ranked_entities": [
                    {
                        "rank": 1,
                        "entity_type": "client_asn",
                        "entity": "64500",
                        "score": 80,
                    }
                ],
            },
            "scorecards": [],
        }
        packet_b = {
            "schema_version": "bot_scorecard_artifacts.v1",
            "artifact_id": "pack-b",
            "index": {
                "schema_version": "bot_scorecard_index.v1",
                "scope": {"request_host": "b.example.com"},
                "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
                "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
                "table_used": "bot_summary_hour",
                "ranked_entities": [
                    {
                        "rank": 1,
                        "entity_type": "client_asn",
                        "entity": "64500",
                        "score": 80,
                    }
                ],
            },
            "scorecards": [
                {
                    "schema_version": "bot_entity_scorecard.v1",
                    "entity_type": "client_asn",
                    "entity": "64500",
                    "scope": {"request_host": "b.example.com"},
                    "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
                    "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
                    "table_used": "bot_summary_hour",
                    "score": 80,
                    "band": "urgent_review",
                    "domain_scores": {},
                    "features": [],
                }
            ],
        }

        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "soc_triage",
            "artifacts": [packet_a, packet_b],
        }

        # Wrapper holds two index artifacts; soc_triage requires exactly one.
        with self.assertRaisesRegex(
            self.render_report.ReportError, "one bot_scorecard_index"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_cross_packet_scorecard_to_standalone_index_mismatch_fails(
        self,
    ) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx-1",
            "scope": {"request_host": "a.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "ranked_entities": [
                {"rank": 1, "entity_type": "client_asn", "entity": "64500", "score": 80}
            ],
        }
        packet = {
            "schema_version": "bot_scorecard_artifacts.v1",
            "artifact_id": "pack-1",
            "scorecards": [
                {
                    "schema_version": "bot_entity_scorecard.v1",
                    "entity_type": "client_asn",
                    "entity": "64500",
                    "scope": {"request_host": "b.example.com"},
                    "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
                    "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
                    "table_used": "bot_summary_hour",
                    "score": 80,
                    "band": "urgent_review",
                    "domain_scores": {},
                    "features": [],
                }
            ],
        }

        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "soc_triage",
            "artifacts": [index, packet],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "metadata mismatch"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_forged_parent_metadata_does_not_make_same_packet(
        self,
    ) -> None:
        packet = {
            "schema_version": "bot_scorecard_artifacts.v1",
            "artifact_id": "pack-1",
            "index": {
                "schema_version": "bot_scorecard_index.v1",
                "ranked_entities": [
                    {
                        "rank": 1,
                        "entity_type": "client_asn",
                        "entity": "64500",
                        "score": 80,
                    }
                ],
            },
            "scorecards": [],
        }
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "forged-scorecard",
            "parent_artifact_id": "pack-1",
            "parent_json_pointer": "/scorecards/0",
            "entity_type": "client_asn",
            "entity": "64500",
            "score": 80,
            "band": "urgent_review",
            "domain_scores": {"security_evidence": 80},
            "features": [],
        }
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "soc_triage",
            "artifacts": [packet, scorecard],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError,
            "Standalone scorecard pairing requires known scope metadata",
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_duplicate_no_id_artifact_bodies_dedupe_when_safe(
        self,
    ) -> None:
        packet = {
            "schema_version": "bot_scorecard_artifacts.v1",
            "scorecards": [],
        }
        posture = {
            "schema_version": "bot_posture_movement.v1",
            "scope": {"request_host": "www.example.com"},
            "metrics": [],
        }
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [packet, copy.deepcopy(packet), posture],
        }

        output, warnings = self.render_report.render(wrapper, self.render_args())

        self.assertIn("Executive Summary", output)
        self.assertTrue(
            any("Ignored duplicate artifact bodies" in warning for warning in warnings),
            f"expected duplicate warning: {warnings}",
        )
        self.assertNotIn("### Artifact artifact\\-2", output)

    def test_render_report_duplicate_primary_no_id_bodies_fail_selection(
        self,
    ) -> None:
        posture = {
            "schema_version": "bot_posture_movement.v1",
            "scope": {"request_host": "www.example.com"},
            "metrics": [],
        }
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [posture, copy.deepcopy(posture)],
        }

        with self.assertRaisesRegex(self.render_report.ReportError, "selection"):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_duplicate_explicit_artifact_bodies_fail(self) -> None:
        posture = {
            "schema_version": "bot_posture_movement.v1",
            "scope": {"request_host": "www.example.com"},
            "metrics": [],
        }
        first = dict(posture, artifact_id="posture-1")
        second = dict(posture, artifact_id="posture-2")
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [first, second],
        }

        with self.assertRaisesRegex(self.render_report.ReportError, "identical"):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_duplicate_referenced_artifact_bodies_fail(self) -> None:
        posture = {
            "schema_version": "bot_posture_movement.v1",
            "scope": {"request_host": "www.example.com"},
            "metrics": [{"name": "requests", "current": 10}],
        }
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [posture, copy.deepcopy(posture)],
            "analyst_notes": [
                {
                    "author_type": "llm",
                    "text": "Compare duplicate inputs.",
                    "data_sources": [
                        {"artifact_id": "artifact-2", "json_pointer": "/metrics/0"}
                    ],
                }
            ],
        }

        with self.assertRaisesRegex(self.render_report.ReportError, "citations"):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_duplicate_scorecard_bodies_fail_when_rows_are_ranked(
        self,
    ) -> None:
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "entity_type": "request_host",
            "entity": "www.example.com",
            "score": 20,
            "band": "watch",
            "domain_scores": {"crawler_governance": 12},
            "features": [
                {
                    "domain": "crawler_governance",
                    "name": "ai_crawler_growth_high",
                    "points": 12,
                    "evidence": "x",
                }
            ],
        }
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "crawler_governance",
            "artifacts": [scorecard, copy.deepcopy(scorecard)],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "input order or rendered rows"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_duplicate_entity_scorecards_make_index_pairing_ambiguous(
        self,
    ) -> None:
        shared = {
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "comparison_type": "previous_window",
        }
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx",
            **shared,
            "ranked_entities": [
                {"rank": 1, "entity_type": "client_asn", "entity": "64500", "score": 80}
            ],
        }
        first = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc-1",
            "entity_type": "client_asn",
            "entity": "64500",
            **shared,
            "score": 80,
            "band": "urgent_review",
            "domain_scores": {"security_evidence": 80},
            "features": [],
        }
        second = {
            **first,
            "artifact_id": "sc-2",
            "score": 70,
            "domain_scores": {"security_evidence": 70},
        }

        with self.assertRaisesRegex(self.render_report.ReportError, "ambiguous"):
            self.render_report.render(
                [index, first, second], self.render_args(report_type="soc_triage")
            )

    def test_render_report_ambiguous_primary_artifact_fails(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "p1",
                    "scope": {"request_host": "a.example.com"},
                    "metrics": [],
                },
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "p2",
                    "scope": {"request_host": "b.example.com"},
                    "metrics": [],
                },
            ],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "requires one bot_posture_movement"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_reserved_child_suffix_in_explicit_id_fails(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "posture-1#index",
                    "metrics": [],
                }
            ],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "reserved generated child"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_empty_explicit_artifact_id_fails(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "  ",
                    "metrics": [],
                }
            ],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "artifact_id must be a non-empty string"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_non_string_explicit_artifact_id_fails(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": 123,
                    "metrics": [],
                }
            ],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "artifact_id must be a non-empty string"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_normalized_artifact_id_collision_fails(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {"schema_version": "bot_posture_movement.v1", "metrics": []},
                {
                    "schema_version": "bot_mover_attribution.v1",
                    "artifact_id": "artifact-1",
                    "movers": [],
                },
            ],
        }

        with self.assertRaisesRegex(
            self.render_report.ReportError, "Duplicate normalized artifact_id"
        ):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_optional_posture_omitted_on_incompatibility(self) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "ranked_entities": [
                {"rank": 1, "entity_type": "client_asn", "entity": "64500", "score": 80}
            ],
        }
        mover = {
            "schema_version": "bot_mover_attribution.v1",
            "artifact_id": "mover",
            "scope": {"request_host": "other.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "dimension": "client_asn",
            "metric": "requests",
            "movers": [],
        }

        output, warnings = self.render_report.render(
            [index, mover], self.render_args(report_type="soc_triage")
        )

        self.assertNotIn("## Movers", output)
        self.assertTrue(
            any("Omitting optional mover" in w for w in warnings),
            f"expected mover-omission warning: {warnings}",
        )

    def test_render_report_control_review_omits_incompatible_optional_posture(
        self,
    ) -> None:
        control = {
            "schema_version": "bot_control_review.v1",
            "artifact_id": "control",
            "scope": {"request_host": "www.example.com"},
            "comparison_type": "post_change_vs_expected",
            "table_used": "bot_siem_summary_day",
            "target": {"policy_id": "policy-1"},
            "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
            "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "target_effects": [],
        }
        posture = {
            "schema_version": "bot_posture_movement.v1",
            "artifact_id": "posture",
            "scope": {"request_host": "other.example.com"},
            "comparison_type": "previous_window",
            "table_used": "bot_summary_day",
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "metrics": [],
        }

        output, warnings = self.render_report.render(
            [control, posture], self.render_args(report_type="control_review")
        )

        self.assertIn("Control Review Summary", output)
        self.assertTrue(
            any("Omitting optional posture" in warning for warning in warnings),
            f"expected posture-omission warning: {warnings}",
        )

    def test_render_report_wrapper_rejects_non_string_scope_label(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "scope_label": 123,
            "artifacts": [{"schema_version": "bot_posture_movement.v1", "metrics": []}],
        }
        with self.assertRaisesRegex(self.render_report.ReportError, "scope_label"):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_markdown_escapes_user_controlled_metacharacters(
        self,
    ) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "title": "Posture *Bold* [Link](http://x) ![img](y) `code` <script> & {braces} (parens) _em_",
            "scope_label": "prop|policy#a.b-c+d!",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "posture-1",
                    "scope": {"request_host": "www.example.com"},
                    "metrics": [
                        {
                            "name": "bad*name_with|pipe",
                            "current": 5,
                            "baseline": 1,
                        }
                    ],
                }
            ],
        }
        output, _ = self.render_report.render(wrapper, self.render_args())
        # Metacharacters are escaped in the rendered Markdown
        self.assertIn("\\*Bold\\*", output)
        self.assertIn("\\[Link\\]", output)
        self.assertIn("\\!\\[img\\]", output)
        self.assertIn("\\`code\\`", output)
        self.assertIn("&lt;script&gt;", output)
        self.assertIn("&amp;", output)
        self.assertIn("\\{braces\\}", output)
        self.assertIn("\\(parens\\)", output)
        self.assertIn("\\_em\\_", output)
        # Pipes inside table cells are escaped
        self.assertIn("bad\\*name\\_with\\|pipe", output)
        # Scope label metacharacters are escaped
        self.assertIn("prop\\|policy\\#a\\.b\\-c\\+d\\!", output)
        # Ensure raw angle-bracket HTML does not appear
        self.assertNotIn("<script>", output)
        # Ensure raw link syntax did not survive
        self.assertNotIn("[Link](http://x)", output)

    def test_render_report_markdown_escapes_table_cell_linebreaks(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "posture-1",
                    "metrics": [
                        {
                            "name": "line1\nline2\rline3",
                            "current": 1,
                            "baseline": 1,
                        }
                    ],
                }
            ],
        }
        output, _ = self.render_report.render(wrapper, self.render_args())
        self.assertIn("line1 line2 line3", output)

    def test_render_report_markdown_escapes_user_backticks_outside_code_spans(
        self,
    ) -> None:
        artifact = {
            "schema_version": "bot_control_review.v1",
            "artifact_id": "id`with`tick",
            "change_time": "2026-04-01T00:00:00Z",
            "target": {"policy_id": "policy`123`"},
            "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
            "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "target_effects": [
                {
                    "metric": "siem_blocked_requests",
                    "before": 100,
                    "after": 130,
                    "expected": 100,
                }
            ],
        }
        output, _ = self.render_report.render(
            artifact, self.render_args(report_type="control_review")
        )
        self.assertIn("Target: \\{\"policy\\_id\": \"policy\\`123\\`\"\\}", output)
        self.assertIn("### Artifact id\\`with\\`tick", output)
        self.assertNotIn("Target: `", output)
        self.assertNotIn("### Artifact `", output)

    def test_render_report_html_strips_backslash_escapes_from_visible_text(
        self,
    ) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "title": "Report *x*",
            "artifacts": [{"schema_version": "bot_posture_movement.v1", "metrics": []}],
        }
        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))
        # HTML output displays the literal star rather than the Markdown escape
        self.assertIn("<h1>Report *x*</h1>", output)
        # Raw Markdown emphasis markup must not survive to HTML
        self.assertNotIn("<em>x</em>", output)

    def test_render_report_html_does_not_render_escaped_user_markdown(
        self,
    ) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "posture-1",
                    "metrics": [
                        {
                            "name": "metric _em_ `code` [link](https://x.test)",
                            "current": 5,
                            "baseline": 1,
                        }
                    ],
                }
            ],
            "analyst_notes": [
                {
                    "note_id": "note-1",
                    "author_type": "llm",
                    "text": "note _em_ `code` [link](https://x.test)",
                    "data_sources": [
                        {
                            "artifact_id": "posture-1",
                            "json_pointer": "/metrics/0/name",
                            "label": "metric label",
                        }
                    ],
                }
            ],
        }
        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))
        self.assertIn("metric _em_ `code` [link](https://x.test)", output)
        self.assertIn("note _em_ `code` [link](https://x.test)", output)
        self.assertNotIn("<em>em</em>", output)
        self.assertNotIn("<code>code</code>", output)
        self.assertNotIn("<a ", output)

    def test_render_report_evidence_limits_include_artifact_detail(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "scorecard_brief",
            "artifacts": [
                {
                    "schema_version": "bot_entity_scorecard.v1",
                    "artifact_id": "card-1",
                    "entity_type": "request_host",
                    "entity": "www.example.com",
                    "scope": {"request_host": "www.example.com"},
                    "table_used": "bot_summary_hour",
                    "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
                    "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
                    "confidence": "medium",
                    "confidence_reasons": ["sparse_counts"],
                    "interpretation_constraints": ["rule_based_scorecard"],
                    "score": 10,
                    "band": "watch",
                    "domain_scores": {"security_evidence": 10},
                    "features": [],
                    "not_evaluated_features": [
                        {
                            "domain": "security_evidence",
                            "name": "siem_blocked_present",
                            "missing_inputs": ["siem_blocked_requests"],
                            "reason": "siem_unavailable",
                        }
                    ],
                }
            ],
        }
        output, _ = self.render_report.render(wrapper, self.render_args())
        self.assertIn("## Evidence Limits", output)
        self.assertIn("### Artifact card\\-1", output)
        self.assertIn("- Schema: bot\\_entity\\_scorecard\\.v1", output)
        self.assertIn("- Table: bot\\_summary\\_hour", output)
        self.assertIn("- Confidence: medium", output)
        self.assertIn("- Confidence reasons: sparse\\_counts", output)
        self.assertIn("- Interpretation constraints: rule\\_based\\_scorecard", output)
        self.assertIn("- Not-evaluated features:", output)
        self.assertIn("siem\\_blocked\\_present", output)
        self.assertIn("siem\\_blocked\\_requests", output)
        self.assertIn("siem\\_unavailable", output)

    def test_render_report_evidence_limits_include_parent_metadata(self) -> None:
        artifacts = self.scorecard.build_artifacts(
            {
                "entity_type": "request_path_norm",
                "table_used": "bot_agg_path_hour",
                "scope": {"request_host": "www.example.com"},
                "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
                "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
                "rows": [
                    {
                        "request_path_norm": "/api/search",
                        "current_requests": 5000,
                        "baseline_requests": 1000,
                    }
                ],
            }
        )
        artifacts["artifact_id"] = "pack"
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "soc_triage",
            "artifacts": [artifacts],
        }
        output, _ = self.render_report.render(wrapper, self.render_args())
        self.assertIn("### Artifact pack\\#index", output)
        self.assertIn("- Parent: pack at /index", output)
        self.assertIn("### Artifact pack\\#scorecard\\-1", output)
        self.assertIn("- Parent: pack at /scorecards/0", output)

    def test_render_report_evidence_limits_include_producer_limit_metadata(
        self,
    ) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "soc_triage",
            "artifacts": [
                {
                    "schema_version": "bot_scorecard_index.v1",
                    "artifact_id": "idx",
                    "producer_limit": 10,
                    "result_row_count": 10,
                    "result_truncated": True,
                    "ranked_entities": [
                        {
                            "rank": 1,
                            "entity_type": "client_asn",
                            "entity": "64500",
                            "score": 80,
                        }
                    ],
                }
            ],
        }
        output, _ = self.render_report.render(wrapper, self.render_args())
        self.assertIn("Producer limits: result\\_row\\_count=10", output)
        self.assertIn("producer\\_limit=10", output)
        self.assertIn("result\\_truncated=true", output)
        self.assertIn(
            "Source population caveat: producer did not provide full source\\-population metadata",
            output,
        )

    def test_render_report_control_review_warns_missing_expected_basis(self) -> None:
        artifact = {
            "schema_version": "bot_control_review.v1",
            "artifact_id": "ctrl",
            "change_time": "2026-04-01T00:00:00Z",
            "target": {"policy_id": "policy-123"},
            "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
            "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "target_effects": [
                {
                    "metric": "siem_blocked_requests",
                    "before": 100,
                    "after": 130,
                    "expected": 100,
                }
            ],
        }
        _, warnings = self.render_report.render(
            artifact, self.render_args(report_type="control_review")
        )
        self.assertTrue(
            any("missing or unknown expected_basis" in w for w in warnings),
            f"missing expected_basis warning absent: {warnings}",
        )

    def test_render_report_control_review_warns_unknown_expected_basis(self) -> None:
        artifact = {
            "schema_version": "bot_control_review.v1",
            "artifact_id": "ctrl",
            "change_time": "2026-04-01T00:00:00Z",
            "target": {"policy_id": "policy-123"},
            "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
            "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "expected_basis": "forecast_model",
            "target_effects": [
                {
                    "metric": "siem_blocked_requests",
                    "before": 100,
                    "after": 130,
                    "expected": 100,
                }
            ],
        }
        _, warnings = self.render_report.render(
            artifact, self.render_args(report_type="control_review")
        )
        self.assertTrue(
            any("missing or unknown expected_basis" in w for w in warnings),
            f"unknown expected_basis warning absent: {warnings}",
        )

    def test_render_report_control_review_warns_missing_expected_window(self) -> None:
        artifact = {
            "schema_version": "bot_control_review.v1",
            "artifact_id": "ctrl",
            "change_time": "2026-04-01T00:00:00Z",
            "target": {"policy_id": "policy-123"},
            "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
            "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "expected_basis": "external_model",
            "target_effects": [{"metric": "m", "before": 1, "after": 2, "expected": 1}],
        }
        _, warnings = self.render_report.render(
            artifact, self.render_args(report_type="control_review")
        )
        self.assertTrue(
            any(
                "missing expected_window metadata for expected_basis external_model"
                in w
                for w in warnings
            ),
            f"missing expected_window warning absent: {warnings}",
        )

    def test_render_report_control_review_no_expected_basis_warning_without_expected(
        self,
    ) -> None:
        artifact = {
            "schema_version": "bot_control_review.v1",
            "artifact_id": "ctrl",
            "change_time": "2026-04-01T00:00:00Z",
            "target": {"policy_id": "policy-123"},
            "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
            "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "target_effects": [{"metric": "m", "before": 1, "after": 2}],
        }
        _, warnings = self.render_report.render(
            artifact, self.render_args(report_type="control_review")
        )
        self.assertFalse(
            any("expected_basis" in w for w in warnings),
            f"unexpected expected_basis warning: {warnings}",
        )

    def test_render_report_warnings_appear_in_output_and_stderr(self) -> None:
        from io import StringIO

        artifact = {"schema_version": "bot_posture_movement.v1", "metrics": []}
        wrapper_path = Path(ROOT / ".tmp_render_input.json")
        wrapper_path.write_text(json.dumps(artifact), encoding="utf-8")
        saved_argv = sys.argv
        saved_stderr = sys.stderr
        saved_stdout = sys.stdout
        captured_err = StringIO()
        captured_out = StringIO()
        try:
            sys.argv = [
                "render_report.py",
                "--file",
                str(wrapper_path),
                "--report-type",
                "executive_posture",
            ]
            sys.stderr = captured_err
            sys.stdout = captured_out
            result = self.render_report.main()
        finally:
            sys.argv = saved_argv
            sys.stderr = saved_stderr
            sys.stdout = saved_stdout
            wrapper_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)
        self.assertIn("## Warnings", captured_out.getvalue())
        self.assertIn("WARNING:", captured_err.getvalue())
        self.assertIn("missing current_window", captured_err.getvalue())

    def test_render_report_unwritable_output_path_exits_nonzero(self) -> None:
        from io import StringIO

        artifact = {"schema_version": "bot_posture_movement.v1", "metrics": []}
        input_path = Path(ROOT / ".tmp_render_input.json")
        input_path.write_text(json.dumps(artifact), encoding="utf-8")
        saved_argv = sys.argv
        saved_stderr = sys.stderr
        captured_err = StringIO()
        try:
            sys.argv = [
                "render_report.py",
                "--file",
                str(input_path),
                "--report-type",
                "executive_posture",
                "--output",
                "/nonexistent-directory-for-tests/report.md",
            ]
            sys.stderr = captured_err
            result = self.render_report.main()
        finally:
            sys.argv = saved_argv
            sys.stderr = saved_stderr
            input_path.unlink(missing_ok=True)
        self.assertEqual(result, 1)
        self.assertIn("ERROR:", captured_err.getvalue())

    def test_render_report_executive_avoids_causal_language(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "scope": {"request_host": "www.example.com"},
                    "metrics": [
                        {"name": "requests", "current": 1500, "baseline": 1000},
                    ],
                }
            ],
        }
        output, _ = self.render_report.render(wrapper, self.render_args())
        self.assertIn("Movement-only posture", output)
        self.assertNotIn("caused by", output)
        self.assertNotIn("proves", output)

    def test_render_report_soc_renders_missing_feature_evidence(self) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "ranked_entities": [
                {"rank": 1, "entity_type": "client_asn", "entity": "64500", "score": 80}
            ],
        }
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "client_asn",
            "entity": "64500",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "score": 80,
            "band": "urgent_review",
            "confidence": "medium",
            "confidence_reasons": ["sparse_counts"],
            "domain_scores": {"security_evidence": 80},
            "features": [
                {
                    "domain": "security_evidence",
                    "name": "bad_bot_share_high",
                    "points": 80,
                    "evidence": "bad bots",
                }
            ],
            "not_evaluated_features": [
                {
                    "domain": "security_evidence",
                    "name": "siem_blocked_present",
                    "missing_inputs": ["siem_blocked_requests"],
                    "reason": "siem_unavailable",
                }
            ],
        }
        output, _ = self.render_report.render(
            [index, scorecard], self.render_args(report_type="soc_triage")
        )
        self.assertIn("Missing Feature Evidence", output)
        self.assertIn("siem\\_blocked\\_present", output)
        self.assertIn("Confidence Notes", output)
        self.assertIn("sparse\\_counts", output)

    def test_render_report_soc_degraded_omits_scorecard_dependent_sections(
        self,
    ) -> None:
        output, _ = self.render_report.render(
            {
                "schema_version": "bot_scorecard_index.v1",
                "ranked_entities": [
                    {
                        "rank": 1,
                        "entity_type": "client_asn",
                        "entity": "64500",
                        "score": 80,
                    }
                ],
            },
            self.render_args(report_type="soc_triage"),
        )
        self.assertNotIn("Missing Feature Evidence", output)
        self.assertNotIn("Confidence Notes", output)
        self.assertNotIn("Domain Score Matrix", output)

    def test_render_report_control_renders_collateral_and_displacement(self) -> None:
        artifact = {
            "schema_version": "bot_control_review.v1",
            "artifact_id": "ctrl",
            "change_time": "2026-04-01T00:00:00Z",
            "target": {"policy_id": "policy-123"},
            "scope": {"request_host": "www.example.com"},
            "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
            "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "expected_window": {"start": "2026-03-25", "end": "2026-04-01"},
            "expected_basis": "before_window",
            "target_effects": [
                {
                    "metric": "siem_blocked_requests",
                    "before": 100,
                    "after": 130,
                    "expected": 100,
                    "status": "increased",
                    "confidence": "high",
                }
            ],
            "collateral_checks": [
                {
                    "metric": "unrelated_auth_fail",
                    "before": 10,
                    "after": 12,
                    "status": "stable",
                    "confidence": "medium",
                }
            ],
            "displacement_checks": [
                {
                    "metric": "other_host_blocked",
                    "before": 20,
                    "after": 60,
                    "status": "increased",
                    "confidence": "low",
                }
            ],
        }
        output, _ = self.render_report.render(
            artifact, self.render_args(report_type="control_review")
        )
        self.assertIn("## Collateral Checks", output)
        self.assertIn("unrelated\\_auth\\_fail", output)
        self.assertIn("## Displacement Checks", output)
        self.assertIn("other\\_host\\_blocked", output)
        self.assertIn("## Confidence", output)
        self.assertIn("Effectiveness review", output)
        self.assertNotIn("caused by", output)
        self.assertNotIn("proves", output)

    def test_render_report_control_empty_collateral_and_displacement_shows_none(
        self,
    ) -> None:
        artifact = {
            "schema_version": "bot_control_review.v1",
            "artifact_id": "ctrl",
            "change_time": "2026-04-01T00:00:00Z",
            "target": {"policy_id": "policy-123"},
            "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
            "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "target_effects": [],
            "collateral_checks": [],
            "displacement_checks": [],
        }
        output, _ = self.render_report.render(
            artifact, self.render_args(report_type="control_review")
        )
        self.assertIn("No collateral checks reported.", output)
        self.assertIn("No displacement checks reported.", output)

    def test_render_report_scorecard_brief_renders_recommended_next_steps(self) -> None:
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "client_asn",
            "entity": "64500",
            "score": 80,
            "band": "urgent_review",
            "domain_scores": {"security_evidence": 80},
            "features": [
                {
                    "domain": "security_evidence",
                    "name": "bad_bot_share_high",
                    "points": 80,
                    "evidence": "x",
                }
            ],
            "recommended_next_steps": [
                "Open policy review ticket",
                "Check WAF logs for ASN 64500",
            ],
        }
        output, _ = self.render_report.render(
            scorecard, self.render_args(report_type="scorecard_brief")
        )
        self.assertIn("## Recommended Next Steps", output)
        self.assertIn("Open policy review ticket", output)
        self.assertIn("Check WAF logs for ASN 64500", output)

    def test_render_report_scorecard_brief_omits_next_steps_when_absent(self) -> None:
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "client_asn",
            "entity": "64500",
            "score": 10,
            "band": "watch",
            "domain_scores": {"security_evidence": 10},
            "features": [],
            "not_evaluated_features": [],
        }
        output, _ = self.render_report.render(
            scorecard, self.render_args(report_type="scorecard_brief")
        )
        self.assertNotIn("Recommended Next Steps", output)

    def test_render_report_crawler_labels_input_order_without_index(self) -> None:
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "request_host",
            "entity": "www.example.com",
            "rowset_scope": {"population": "good_bot"},
            "score": 20,
            "band": "watch",
            "domain_scores": {"crawler_governance": 12},
            "features": [
                {
                    "domain": "crawler_governance",
                    "name": "rate_429_delta_high",
                    "points": 12,
                    "evidence": "good_bot 429s",
                }
            ],
            "not_evaluated_features": [],
        }
        output, _ = self.render_report.render(
            scorecard, self.render_args(report_type="crawler_governance")
        )
        self.assertIn("Rows follow normalized scorecard input order", output)

    def test_render_report_crawler_uses_index_order_when_available(self) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "ranked_entities": [
                {
                    "rank": 1,
                    "entity_type": "request_host",
                    "entity": "www.example.com",
                    "score": 20,
                }
            ],
        }
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "request_host",
            "entity": "www.example.com",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "rowset_scope": {"population": "good_bot"},
            "score": 20,
            "band": "watch",
            "domain_scores": {"crawler_governance": 12},
            "features": [
                {
                    "domain": "crawler_governance",
                    "name": "rate_429_delta_high",
                    "points": 12,
                    "evidence": "good_bot 429s",
                }
            ],
            "not_evaluated_features": [],
        }
        output, _ = self.render_report.render(
            [index, scorecard],
            self.render_args(report_type="crawler_governance"),
        )
        self.assertIn("Rows follow scorecard index order", output)

    def test_render_report_crawler_labels_input_order_when_index_is_unusable(
        self,
    ) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx",
            "ranked_entities": [
                {
                    "rank": 1,
                    "entity_type": "request_host",
                    "entity": "ranked.example.com",
                    "score": 90,
                }
            ],
        }
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "request_host",
            "entity": "input.example.com",
            "score": 20,
            "band": "watch",
            "domain_scores": {"crawler_governance": 12},
            "features": [
                {
                    "domain": "crawler_governance",
                    "name": "ai_crawler_growth_high",
                    "points": 12,
                    "evidence": "AI crawler growth was emitted by the artifact.",
                }
            ],
        }
        output, warnings = self.render_report.render(
            [index, scorecard],
            self.render_args(report_type="crawler_governance"),
        )
        self.assertIn("Rows follow normalized scorecard input order", output)
        self.assertNotIn("Rows follow scorecard index order", output)
        self.assertTrue(
            any("No scorecards were compatible" in warning for warning in warnings),
            f"expected incompatible-index warning: {warnings}",
        )

    def test_render_report_edge_ops_labels_input_order_without_index(self) -> None:
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "request_path_norm",
            "entity": "/api/search",
            "score": 40,
            "band": "watch",
            "domain_scores": {"cache_busting": 40},
            "features": [
                {
                    "domain": "cache_busting",
                    "name": "cache_miss_rate_high",
                    "points": 20,
                    "evidence": "cache miss",
                },
                {
                    "domain": "cache_busting",
                    "name": "querystring_diversity_high",
                    "points": 20,
                    "evidence": "qs",
                },
            ],
            "not_evaluated_features": [],
        }
        output, _ = self.render_report.render(
            scorecard, self.render_args(report_type="edge_ops_impact")
        )
        self.assertIn("Rows follow normalized scorecard input order", output)
        self.assertIn("cache\\_miss\\_rate\\_high", output)

    def test_render_report_edge_ops_labels_input_order_when_index_is_unusable(
        self,
    ) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx",
            "ranked_entities": [
                {
                    "rank": 1,
                    "entity_type": "request_path_norm",
                    "entity": "/ranked",
                    "score": 90,
                }
            ],
        }
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "request_path_norm",
            "entity": "/input",
            "score": 40,
            "band": "watch",
            "domain_scores": {"cache_busting": 40},
            "features": [
                {
                    "domain": "cache_busting",
                    "name": "cache_miss_rate_high",
                    "points": 20,
                    "evidence": "cache miss",
                }
            ],
        }
        output, warnings = self.render_report.render(
            [index, scorecard],
            self.render_args(report_type="edge_ops_impact"),
        )
        self.assertIn("Rows follow normalized scorecard input order", output)
        self.assertNotIn("Rows follow scorecard index order", output)
        self.assertTrue(
            any("No scorecards were compatible" in warning for warning in warnings),
            f"expected incompatible-index warning: {warnings}",
        )

    def test_render_report_edge_ops_degraded_when_no_relevant_evidence(self) -> None:
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "request_host",
            "entity": "www.example.com",
            "score": 10,
            "band": "watch",
            "domain_scores": {"security_evidence": 10},
            "features": [
                {
                    "domain": "security_evidence",
                    "name": "bad_bot_share_high",
                    "points": 10,
                    "evidence": "x",
                }
            ],
            "not_evaluated_features": [],
        }
        output, warnings = self.render_report.render(
            scorecard, self.render_args(report_type="edge_ops_impact")
        )
        self.assertIn("No relevant edge/ops impact evidence available", output)
        self.assertTrue(any("no eligible evaluated" in warning for warning in warnings))

    def test_render_report_crawler_missing_only_warns_about_missing_inputs(
        self,
    ) -> None:
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "request_host",
            "entity": "www.example.com",
            "score": 0,
            "band": "watch",
            "domain_scores": {"crawler_governance": 0},
            "features": [],
            "not_evaluated_features": [
                {
                    "domain": "crawler_governance",
                    "name": "good_bot_429_present",
                    "missing_inputs": ["good_bot_429_requests"],
                    "reason": "good_bot_columns_unavailable",
                }
            ],
        }
        output, warnings = self.render_report.render(
            scorecard, self.render_args(report_type="crawler_governance")
        )
        self.assertIn("No relevant crawler governance evidence available", output)
        self.assertIn("good\\_bot\\_429\\_present", output)
        self.assertTrue(
            any("relevant missing feature inputs" in warning for warning in warnings),
            f"missing-input warning absent: {warnings}",
        )

    def test_render_report_edge_ops_missing_only_warns_about_missing_inputs(
        self,
    ) -> None:
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "request_path_norm",
            "entity": "/api/search",
            "score": 0,
            "band": "watch",
            "domain_scores": {"cache_busting": 0},
            "features": [],
            "not_evaluated_features": [
                {
                    "domain": "cache_busting",
                    "name": "cache_miss_rate_high",
                    "missing_inputs": ["cache_status"],
                    "reason": "cache_columns_unavailable",
                }
            ],
        }
        output, warnings = self.render_report.render(
            scorecard, self.render_args(report_type="edge_ops_impact")
        )
        self.assertIn("No relevant edge/ops impact evidence available", output)
        self.assertIn("cache\\_miss\\_rate\\_high", output)
        self.assertTrue(
            any("relevant missing feature inputs" in warning for warning in warnings),
            f"missing-input warning absent: {warnings}",
        )

    def test_render_report_domain_matrix_renders_numeric_zero(self) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "ranked_entities": [
                {"rank": 1, "entity_type": "client_asn", "entity": "64500", "score": 80}
            ],
        }
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "client_asn",
            "entity": "64500",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bot_summary_hour",
            "score": 80,
            "band": "urgent_review",
            "domain_scores": {"security_evidence": 80, "cache_busting": 0},
            "features": [
                {
                    "domain": "security_evidence",
                    "name": "bad_bot_share_high",
                    "points": 80,
                    "evidence": "x",
                }
            ],
        }
        output, _ = self.render_report.render(
            [index, scorecard], self.render_args(report_type="soc_triage")
        )
        # Zero score rendered as 0, not "unavailable".
        self.assertIn(
            "| Entity | Total score | security\\_evidence | cache\\_busting |",
            output,
        )
        self.assertIn("| 64500 | 80 | 80 | 0 |", output)

    def _posture_wrapper(self, metrics):
        return {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "posture-1",
                    "scope": {"request_host": "www.example.com"},
                    "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
                    "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
                    "metrics": metrics,
                }
            ],
        }

    def test_render_report_html_has_no_external_assets(self) -> None:
        wrapper = self._posture_wrapper(
            [{"name": "requests", "current": 1500, "baseline": 1000}]
        )
        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))
        self.assertNotIn("<script", output)
        self.assertNotIn('src="http', output)
        self.assertNotIn("src='http", output)
        self.assertNotIn('href="http', output)
        self.assertNotIn("href='http", output)
        self.assertNotIn("cdn.", output)
        self.assertNotIn("googleapis", output)
        self.assertNotIn("<link", output)
        self.assertNotIn("<img", output)
        self.assertNotIn("<iframe", output)
        self.assertNotIn("@import", output)
        self.assertIn("<style>", output)

    def test_render_report_html_escapes_user_controlled_svg_text(self) -> None:
        wrapper = self._posture_wrapper(
            [
                {
                    "name": "<script>alert(1)</script>",
                    "current": 10,
                    "baseline": 5,
                }
            ]
        )
        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", output)
        self.assertNotIn("<script>alert(1)</script>", output)

    def test_render_report_html_executive_charts_present(self) -> None:
        wrapper = self._posture_wrapper(
            [
                {
                    "name": "requests",
                    "current": 1500,
                    "baseline": 1000,
                    "absolute_delta": 500,
                    "pct_change": 50,
                    "direction": "increase",
                    "confidence": "high",
                }
            ]
        )
        output, warnings = self.render_report.render(
            wrapper, self.render_args(format="html")
        )
        self.assertIn("Metric Delta Cards", output)
        self.assertIn("Current Versus Baseline Bars", output)
        self.assertIn("direction increase", output)
        self.assertIn("confidence high", output)
        self.assertIn("<svg", output)
        self.assertFalse(
            any("chart skipped" in warning.lower() for warning in warnings)
        )

    def test_render_report_html_soc_charts_present(self) -> None:
        artifacts = self.scorecard.build_artifacts(
            {
                "entity_type": "client_asn",
                "table_used": "bot_agg_asn_hour",
                "scope": {"request_host": "www.example.com"},
                "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
                "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 5000,
                        "baseline_requests": 1000,
                        "current_cache_miss_pct": 80,
                        "baseline_cache_miss_pct": 10,
                    }
                ],
            }
        )
        artifacts["artifact_id"] = "pack"
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "soc_triage",
            "artifacts": [artifacts],
        }
        output, warnings = self.render_report.render(
            wrapper, self.render_args(format="html")
        )
        self.assertIn("Scorecard Ranking Bars", output)
        self.assertIn("Domain Score Matrix", output)
        self.assertIn("Rank 1:", output)
        self.assertIn("<th>security_evidence</th>", output)
        self.assertNotIn("<th>security\\_evidence</th>", output)
        self.assertFalse(
            any("chart skipped" in warning.lower() for warning in warnings),
            f"unexpected chart-skip warnings: {warnings}",
        )

    def test_render_report_html_soc_degraded_skips_domain_matrix(self) -> None:
        output, warnings = self.render_report.render(
            {
                "schema_version": "bot_scorecard_index.v1",
                "ranked_entities": [
                    {
                        "rank": 1,
                        "entity_type": "client_asn",
                        "entity": "64500",
                        "score": 80,
                        "confidence": "medium",
                    }
                ],
            },
            self.render_args(report_type="soc_triage", format="html"),
        )
        self.assertIn("Scorecard Ranking Bars", output)
        self.assertIn("chart skipped because", output)
        self.assertTrue(
            any(
                "Domain Score Matrix" in warning and "skipped" in warning
                for warning in warnings
            ),
            f"expected Domain Score Matrix skip warning: {warnings}",
        )

    def test_render_report_html_control_chart_present(self) -> None:
        artifact = {
            "schema_version": "bot_control_review.v1",
            "artifact_id": "ctrl",
            "change_time": "2026-04-01T00:00:00Z",
            "target": {"policy_id": "policy-123"},
            "before_window": {"start": "2026-03-25", "end": "2026-04-01"},
            "after_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "target_effects": [
                {
                    "metric": "siem_blocked_requests",
                    "before": 100,
                    "after": 130,
                    "expected": 100,
                    "status": "increased",
                    "confidence": "high",
                }
            ],
        }
        output, _ = self.render_report.render(
            artifact,
            self.render_args(report_type="control_review", format="html"),
        )
        self.assertIn("Control Before/After/Expected Bars", output)
        self.assertIn("status increased", output)
        self.assertIn("confidence high", output)
        self.assertIn("<svg", output)

    def test_render_report_html_scorecard_brief_chart_present(self) -> None:
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "sc",
            "entity_type": "client_asn",
            "entity": "64500",
            "score": 80,
            "band": "urgent_review",
            "domain_scores": {"security_evidence": 80, "cache_busting": 20},
            "features": [],
        }
        output, _ = self.render_report.render(
            scorecard,
            self.render_args(report_type="scorecard_brief", format="html"),
        )
        self.assertIn("Domain Scores", output)
        self.assertIn("<svg", output)

    def test_render_report_html_crawler_scorecards_get_score_bars_without_index(
        self,
    ) -> None:
        shared = {
            "schema_version": "bot_entity_scorecard.v1",
            "entity_type": "request_host",
            "rowset_scope": {"population": "good_bot"},
            "band": "watch",
            "primary_domain": "crawler_governance",
            "confidence": "medium",
            "domain_scores": {"crawler_governance": 12},
            "features": [
                {
                    "domain": "crawler_governance",
                    "name": "good_bot_429_present",
                    "points": 12,
                    "evidence": "good bot 429s were emitted by the artifact.",
                }
            ],
        }
        low = {
            **shared,
            "artifact_id": "low",
            "entity": "low.example.com",
            "score": 20,
        }
        high = {
            **shared,
            "artifact_id": "high",
            "entity": "high.example.com",
            "score": 80,
        }
        output, _ = self.render_report.render(
            [low, high],
            self.render_args(report_type="crawler_governance", format="html"),
        )
        self.assertIn("Scorecard Ranking Bars", output)
        self.assertIn("sorted by emitted score", output)
        self.assertNotIn("Rank 1:", output)
        self.assertLess(
            output.index("high.example.com"), output.index("low.example.com")
        )

    def test_render_report_html_mover_chart_present(self) -> None:
        shared_compat = {
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "comparison_type": "previous_window",
            "table_used": "bot_summary_hour",
        }
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "posture-1",
                    **shared_compat,
                    "metrics": [
                        {"name": "requests", "current": 1500, "baseline": 1000}
                    ],
                },
                {
                    "schema_version": "bot_mover_attribution.v1",
                    "artifact_id": "mover-1",
                    **shared_compat,
                    "dimension": "client_asn",
                    "metric": "requests",
                    "total_delta": 500,
                    "movers": [
                        {
                            "value": "64500",
                            "metric": "requests",
                            "absolute_delta": 400,
                            "contribution_pct": 80,
                            "confidence": "medium",
                        },
                        {
                            "value": "64501",
                            "metric": "requests",
                            "absolute_delta": 450,
                            "contribution_pct": 90,
                            "confidence": "high",
                        }
                    ],
                },
            ],
        }
        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))
        self.assertIn("Mover Contribution Bars", output)
        self.assertIn("total delta 500", output)
        self.assertLess(output.index("64501"), output.index("64500"))

    def test_render_report_html_edge_ops_scorecards_get_score_bars_without_index(
        self,
    ) -> None:
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "scorecard-1",
            "entity_type": "request_path_norm",
            "entity": "/api/search",
            "score": 70,
            "band": "review",
            "primary_domain": "cache_busting",
            "confidence": "medium",
            "domain_scores": {"cache_busting": 50, "origin_impact": 20},
            "features": [
                {
                    "name": "cache_miss_rate_high",
                    "domain": "cache_busting",
                    "points": 50,
                    "evidence": "current cache miss rate exceeded threshold",
                }
            ],
        }
        output, _ = self.render_report.render(
            scorecard,
            self.render_args(report_type="edge_ops_impact", format="html"),
        )
        self.assertIn("Scorecard Ranking Bars", output)
        self.assertIn("sorted by emitted score", output)
        self.assertIn("/api/search", output)

    def test_render_report_html_edge_ops_optional_charts_present(self) -> None:
        shared_compat = {
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "comparison_type": "previous_window",
            "table_used": "bot_summary_hour",
        }
        scorecard = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "scorecard-1",
            **shared_compat,
            "entity_type": "client_asn",
            "entity": "64500",
            "score": 70,
            "band": "review",
            "primary_domain": "cache_busting",
            "confidence": "medium",
            "domain_scores": {"cache_busting": 50, "origin_impact": 20},
            "features": [
                {
                    "name": "cache_miss_rate_high",
                    "domain": "cache_busting",
                    "points": 50,
                    "evidence": "current cache miss rate exceeded threshold",
                }
            ],
        }
        posture = {
            "schema_version": "bot_posture_movement.v1",
            "artifact_id": "posture-1",
            **shared_compat,
            "metrics": [
                {
                    "name": "cache_miss_pct",
                    "current": 35,
                    "baseline": 10,
                    "direction": "increase",
                    "confidence": "high",
                }
            ],
        }
        mover = {
            "schema_version": "bot_mover_attribution.v1",
            "artifact_id": "mover-1",
            **shared_compat,
            "dimension": "client_asn",
            "metric": "cache_miss_requests",
            "total_delta": 250,
            "movers": [
                {
                    "value": "64500",
                    "metric": "cache_miss_requests",
                    "absolute_delta": 250,
                    "contribution_pct": 100,
                    "confidence": "high",
                }
            ],
        }
        output, warnings = self.render_report.render(
            [scorecard, posture, mover],
            self.render_args(report_type="edge_ops_impact", format="html"),
        )
        self.assertIn("Domain Score Matrix", output)
        self.assertIn("Current Versus Baseline Bars", output)
        self.assertIn("Mover Contribution Bars", output)
        self.assertIn("total delta 250", output)
        self.assertFalse(
            any("chart skipped" in warning.lower() for warning in warnings),
            f"unexpected chart-skip warnings: {warnings}",
        )

    def test_render_report_html_empty_metrics_emits_skip_and_warning(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "metrics": [],
                }
            ],
        }
        output, warnings = self.render_report.render(
            wrapper, self.render_args(format="html")
        )
        self.assertIn("chart skipped because", output)
        self.assertTrue(
            any(
                "Metric Delta Cards" in warning and "skipped" in warning
                for warning in warnings
            ),
            f"expected Metric Delta Cards skip warning: {warnings}",
        )
        self.assertTrue(
            any(
                "Current Versus Baseline Bars" in warning and "skipped" in warning
                for warning in warnings
            ),
            f"expected Current Versus Baseline Bars skip warning: {warnings}",
        )

    def test_render_report_html_non_numeric_metric_values_skip_bars(self) -> None:
        wrapper = self._posture_wrapper(
            [{"name": "requests", "current": "n/a", "baseline": "n/a"}]
        )
        output, warnings = self.render_report.render(
            wrapper, self.render_args(format="html")
        )
        self.assertIn("chart skipped because", output)
        self.assertTrue(
            any(
                "Current Versus Baseline Bars" in warning and "skipped" in warning
                for warning in warnings
            ),
            f"expected Current Versus Baseline Bars skip warning: {warnings}",
        )

    def _load_example(self, name: str) -> dict:
        path = ROOT / "skills/bot-insights/examples" / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_example_executive_posture_renders(self) -> None:
        wrapper = self._load_example("executive-posture")
        markdown, warnings = self.render_report.render(wrapper, self.render_args())
        self.assertEqual(warnings, [])
        self.assertIn("Executive Summary", markdown)
        self.assertIn("Movers", markdown)
        self.assertIn("Analyst Notes", markdown)
        html, warnings = self.render_report.render(
            wrapper, self.render_args(format="html")
        )
        self.assertEqual(warnings, [])
        self.assertIn("<svg", html)

    def test_example_soc_triage_renders(self) -> None:
        wrapper = self._load_example("soc-triage")
        markdown, warnings = self.render_report.render(wrapper, self.render_args())
        self.assertEqual(warnings, [])
        self.assertIn("Top Risky Entities", markdown)
        html, _ = self.render_report.render(
            wrapper, self.render_args(format="html")
        )
        self.assertIn("<svg", html)
        self.assertIn("scorecard-pack-1#scorecard-1", html)

    def test_example_control_review_renders(self) -> None:
        wrapper = self._load_example("control-review")
        markdown, warnings = self.render_report.render(wrapper, self.render_args())
        self.assertEqual(warnings, [])
        self.assertIn("Control Review Summary", markdown)
        self.assertIn("Before/After/Expected", markdown)

    def test_example_crawler_governance_renders(self) -> None:
        wrapper = self._load_example("crawler-governance")
        markdown, warnings = self.render_report.render(wrapper, self.render_args())
        self.assertIn("Crawler Governance", markdown)
        self.assertTrue(
            any("missing feature inputs" in warning for warning in warnings),
            f"expected crawler-governance missing feature warning: {warnings}",
        )


if __name__ == "__main__":
    unittest.main()
