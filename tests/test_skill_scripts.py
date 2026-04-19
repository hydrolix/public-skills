from __future__ import annotations

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

    def test_scorecard_does_not_synthesize_contribution_for_limited_rowset(self) -> None:
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
        self.assertEqual(missing["contribution_to_total_delta_high"], ["contribution_pct"])

    def test_scorecard_synthesizes_contribution_for_explicit_complete_rowset(self) -> None:
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
            feature["name"]: feature
            for feature in by_entity["64500"]["features"]
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
                "rows": [{"request_host": "www.example.com", "current_requests": 1000, "baseline_requests": 900}],
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
                    {"request_host": "a.example.com", "current_requests": 5000, "baseline_requests": 100},
                    {"request_host": "b.example.com", "current_requests": 4000, "baseline_requests": 100},
                    {"request_host": "c.example.com", "current_requests": 3000, "baseline_requests": 100},
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
                    {"start": "2026-03-25", "end": "2026-04-01", "label": "previous_week"}
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

        output, warnings = self.render_report.render(wrapper, self.render_args(format="html"))

        self.assertIn("SOC &lt;Report&gt;", output)
        self.assertIn("Review &lt;this&gt; path.", output)
        self.assertIn("scorecard-pack#scorecard-1", output)
        self.assertIn("<svg", output)
        self.assertEqual(warnings, [])

    def test_render_report_raw_array_requires_report_type(self) -> None:
        with self.assertRaisesRegex(self.render_report.ReportError, "requires --report-type"):
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

        with self.assertRaisesRegex(self.render_report.ReportError, "requires bot_entity_scorecard"):
            self.render_report.render(packet, self.render_args(report_type="crawler_governance"))

    def test_render_report_rejects_incompatible_standalone_scorecard_pairing(self) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "scope": {"request_host": "a.example.com"},
            "comparison_type": "previous_window",
            "table_used": "bot_summary_hour",
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "ranked_entities": [{"entity_type": "client_asn", "entity": "64500", "score": 80}],
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

        with self.assertRaisesRegex(self.render_report.ReportError, "metadata mismatch"):
            self.render_report.render([index, scorecard], self.render_args(report_type="soc_triage"))

    def test_render_report_unresolved_analyst_citation_fails(self) -> None:
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [{"schema_version": "bot_posture_movement.v1", "metrics": []}],
            "analyst_notes": [
                {
                    "author_type": "llm",
                    "text": "Review this.",
                    "data_sources": [{"schema_version": "bot_posture_movement.v1", "json_pointer": "/missing"}],
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
                    "data_sources": [{"schema_version": "bot_posture_movement.v1", "json_pointer": "/metrics~2bad"}],
                }
            ],
        }

        with self.assertRaisesRegex(self.render_report.ReportError, "pointer /metrics~2bad"):
            self.render_report.render(wrapper, self.render_args())

    def test_render_report_crawler_generic_rates_require_structured_provenance(self) -> None:
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
        self.assertTrue(any("no eligible evaluated" in warning for warning in warnings))

        with_provenance = dict(base_card)
        with_provenance["rowset_scope"] = {"population": "good_bot"}
        output, warnings = self.render_report.render(
            with_provenance,
            self.render_args(report_type="crawler_governance"),
        )
        self.assertIn("rate_429_delta_high", output)

    def test_render_report_raw_entity_scorecard_requires_report_type(self) -> None:
        with self.assertRaisesRegex(self.render_report.ReportError, "Missing or ambiguous"):
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


if __name__ == "__main__":
    unittest.main()
