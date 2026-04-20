from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
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
        cls.scorecard = load_module(
            "scorecard",
            ROOT / "skills/bot-insights/scripts/scorecard.py",
        )
        cls.cache_origin_impact = load_module(
            "cache_origin_impact",
            ROOT / "skills/bot-insights/scripts/cache_origin_impact.py",
        )

    def cache_origin_payload(self, **overrides):
        payload = {
            "analysis_type": "cache_busting_origin_impact",
            "comparison_type": "previous_window",
            "granularity": "hour",
            "table_used": "bot_agg_path_hour",
            "summary_table_used": True,
            "scope": {"request_host": "www.example.com"},
            "dimensions": ["request_path_norm", "bot_class"],
            "current_window": {
                "start": "2026-04-18T12:00:00Z",
                "end": "2026-04-18T18:00:00Z",
            },
            "metric_semantics": {
                "unique_query_strings": "exact_period_unique",
                "contribution_fields": "complete_scope_pre_limit",
            },
            "rows": [
                {
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "current_requests": 1000,
                    "baseline_requests": 800,
                    "current_unique_query_strings": 700,
                    "baseline_unique_query_strings": 300,
                    "cache_miss_contribution_pct": 25,
                }
            ],
        }
        payload.update(overrides)
        return payload

    def cache_origin_feature_names(self, candidate):
        return {feature["name"] for feature in candidate["features"]}

    def cache_origin_script_path(self):
        return ROOT / "skills/bot-insights/scripts/cache_origin_impact.py"

    def run_cache_origin_cli(self, args=None, input_text=None):
        command = [sys.executable, str(self.cache_origin_script_path())]
        if args:
            command.extend(args)
        return subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )

    def cache_origin_e2e_payload(self):
        return self.cache_origin_payload(
            baseline_windows=[
                {
                    "start": "2026-04-18T06:00:00Z",
                    "end": "2026-04-18T12:00:00Z",
                    "label": "previous_6_hours",
                }
            ],
            scope={
                "request_host": "www.example.com",
                "selected_bot_classes": ["bad", "unknown"],
            },
            dimensions=["request_path_norm", "bot_class", "asn_type"],
            metric_semantics={
                "uniq_qs": "exact_period_unique",
                "origin_p95_ms": "metadata_merged_quantile",
                "origin_p99_ms": "metadata_merged_quantile",
                "contribution_fields": "complete_scope_pre_limit",
            },
            bot_summary_context={
                "scope": {"request_host": "www.example.com"},
                "metrics": {
                    "host_bot_traffic_share_pct": 42.1,
                    "host_ai_category_share_pct": 7.4,
                },
            },
            rows=[
                {
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "asn_type": "hosting",
                    "current_requests": 10000,
                    "baseline_requests": 10000,
                    "current_cache_misses": 9000,
                    "baseline_cache_misses": 7000,
                    "current_uniq_qs": 8500,
                    "baseline_uniq_qs": 4500,
                    "current_origin_p95_ms": 360,
                    "baseline_origin_p95_ms": 120,
                    "current_origin_p99_ms": 900,
                    "baseline_origin_p99_ms": 500,
                    "current_total_cache_misses_for_share": 10000,
                    "current_selected_bot_class_cache_misses_for_share": 9000,
                    "current_total_origin_pressure_for_path": 3600,
                    "current_selected_bot_class_origin_pressure_for_path": 3240,
                    "current_total_cache_misses_for_contribution": 20000,
                    "current_total_origin_pressure_for_contribution": 18000,
                    "cache_miss_contribution_pct": 45,
                    "origin_pressure_contribution_pct": 18,
                }
            ],
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

    def test_cache_origin_mcp_rows_mapping(self) -> None:
        payload = self.cache_origin_payload(
            columns=[
                "request_path_norm",
                "bot_class",
                "current_requests",
                "baseline_requests",
            ],
            rows=[
                ["/api/search", "bad", 1200, 800],
                ["/api/catalog", "unknown", 900, 850],
            ],
            metric_semantics={},
        )

        rows = self.cache_origin_impact.result_rows(payload)
        self.assertEqual(rows[0]["request_path_norm"], "/api/search")
        result = self.cache_origin_impact.build_report(payload)

        self.assertEqual(result["schema_version"], "cache_origin_impact_report.v1")
        self.assertEqual(result["candidates"][0]["current"]["requests"], 1200)
        self.assertEqual(result["candidates"][0]["baseline"]["requests"], 800)

    def test_cache_origin_derives_canonical_metrics_and_deltas(self) -> None:
        payload = self.cache_origin_payload(
            baseline_windows=[
                {
                    "start": "2026-04-18T06:00:00Z",
                    "end": "2026-04-18T12:00:00Z",
                }
            ],
            metric_semantics={
                "unique_query_strings": "exact_period_unique",
                "origin_p95_ms": "metadata_merged_quantile",
                "origin_p99_ms": "metadata_merged_quantile",
            },
            rows=[
                {
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "current_cnt_all": 1000,
                    "baseline_cnt_all": 800,
                    "current_cnt_cache_miss": 500,
                    "baseline_cnt_cache_miss": 200,
                    "current_uniq_qs": 600,
                    "baseline_uniq_qs": 240,
                    "current_p95_origin_ttfb": 300,
                    "baseline_p95_origin_ttfb": 200,
                    "current_p99_origin_ttfb": 900,
                    "baseline_p99_origin_ttfb": 700,
                    "current_response_total_bytes": 2048,
                    "baseline_response_total_bytes": 1024,
                }
            ],
        )

        result = self.cache_origin_impact.build_report(payload)
        candidate = result["candidates"][0]

        self.assertEqual(candidate["current"]["requests"], 1000)
        self.assertEqual(candidate["current"]["cache_misses"], 500)
        self.assertEqual(candidate["current"]["unique_query_strings"], 600)
        self.assertEqual(candidate["current"]["origin_p95_ms"], 300)
        self.assertEqual(candidate["current"]["origin_p99_ms"], 900)
        self.assertEqual(candidate["current"]["response_bytes"], 2048)
        self.assertEqual(candidate["current"]["miss_rate_pct"], 50)
        self.assertEqual(candidate["current"]["qs_diversity_ratio"], 0.6)
        self.assertEqual(candidate["current"]["origin_pressure_score"], 150)
        self.assertEqual(candidate["baseline"]["miss_rate_pct"], 25)
        self.assertEqual(candidate["deltas"]["requests"], 200)
        self.assertEqual(candidate["deltas"]["cache_misses"], 300)
        self.assertEqual(candidate["deltas"]["miss_rate_delta_pp"], 25)
        self.assertEqual(candidate["deltas"]["qs_diversity_delta"], 0.3)
        self.assertEqual(candidate["deltas"]["origin_p95_delta_ms"], 100)
        self.assertEqual(candidate["deltas"]["origin_p99_delta_ms"], 200)
        self.assertEqual(candidate["deltas"]["cache_miss_pct_change"], 150)
        self.assertEqual(candidate["deltas"]["origin_p95_pct_change"], 50)
        self.assertEqual(candidate["deltas"]["origin_pressure_delta"], 110)

    def test_cache_origin_zero_denominators_do_not_create_rates(self) -> None:
        payload = self.cache_origin_payload(
            baseline_windows=[
                {
                    "start": "2026-04-18T06:00:00Z",
                    "end": "2026-04-18T12:00:00Z",
                }
            ],
            rows=[
                {
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "current_requests": 0,
                    "baseline_requests": 0,
                    "current_cache_misses": 5,
                    "baseline_cache_misses": 0,
                    "current_unique_query_strings": 10,
                    "baseline_unique_query_strings": 0,
                }
            ],
        )

        candidate = self.cache_origin_impact.build_report(payload)["candidates"][0]

        self.assertNotIn("miss_rate_pct", candidate["current"])
        self.assertNotIn("qs_diversity_ratio", candidate["current"])
        self.assertEqual(candidate["deltas"]["cache_miss_pct_change"], 500)

    def test_cache_origin_normalizes_unequal_baseline_windows(self) -> None:
        payload = self.cache_origin_payload(
            current_window={
                "start": "2026-04-18T12:00:00Z",
                "end": "2026-04-18T18:00:00Z",
            },
            baseline_windows=[
                {
                    "start": "2026-04-18T00:00:00Z",
                    "end": "2026-04-18T12:00:00Z",
                }
            ],
            metric_semantics={
                "unique_query_strings": "exact_period_unique",
                "origin_p95_ms": "metadata_merged_quantile",
            },
            rows=[
                {
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "current_requests": 600,
                    "baseline_requests": 1000,
                    "current_cache_misses": 300,
                    "baseline_cache_misses": 400,
                    "current_unique_query_strings": 900,
                    "baseline_unique_query_strings": 1000,
                    "current_origin_p95_ms": 500,
                    "baseline_origin_p95_ms": 200,
                    "current_response_bytes": 3000,
                    "baseline_response_bytes": 4000,
                }
            ],
        )

        result = self.cache_origin_impact.build_report(payload)
        candidate = result["candidates"][0]

        self.assertEqual(
            result["baseline_normalization"]["method"],
            "duration_normalized_additive_metrics",
        )
        self.assertEqual(result["baseline_normalization"]["factor"], 0.5)
        self.assertEqual(
            result["baseline_normalization"]["applies_to"],
            ["cache_misses", "requests", "response_bytes"],
        )
        self.assertEqual(candidate["baseline"]["requests"], 500)
        self.assertEqual(candidate["baseline"]["cache_misses"], 200)
        self.assertEqual(candidate["baseline"]["response_bytes"], 2000)
        self.assertEqual(candidate["baseline"]["unique_query_strings"], 1000)
        self.assertEqual(candidate["baseline"]["miss_rate_pct"], 40)
        self.assertEqual(candidate["baseline"]["origin_pressure_score"], 40)
        self.assertEqual(candidate["deltas"]["origin_pressure_delta"], 110)

    def test_cache_origin_qs_semantics_control_ratio_clamping(self) -> None:
        base_rows = [
            {
                "request_path_norm": "/api/search",
                "bot_class": "bad",
                "current_requests": 100,
                "baseline_requests": 100,
                "current_unique_query_strings": 150,
                "baseline_unique_query_strings": 50,
            }
        ]
        exact = self.cache_origin_payload(rows=base_rows)
        approximate = self.cache_origin_payload(
            rows=base_rows,
            metric_semantics={"unique_query_strings": "bucket_summed_unique"},
        )

        exact_candidate = self.cache_origin_impact.build_report(exact)["candidates"][0]
        approximate_report = self.cache_origin_impact.build_report(approximate)
        approximate_candidate = approximate_report["candidates"][0]

        self.assertEqual(exact_candidate["current"]["qs_diversity_ratio"], 1)
        self.assertEqual(approximate_candidate["current"]["qs_diversity_ratio"], 1.5)
        self.assertIn(
            "query_string_cardinality_approximate",
            approximate_candidate["confidence_reasons"],
        )
        self.assertIn(
            "query_string_cardinality_approximate",
            approximate_report["confidence_reasons"],
        )
        self.assertIn(
            "query_string_cardinality_approximate",
            approximate_candidate["limitations"],
        )

    def test_cache_origin_qs_semantics_aliases_control_exact_ratio_clamping(self) -> None:
        rows = [
            {
                "request_path_norm": "/api/search",
                "bot_class": "bad",
                "current_requests": 100,
                "baseline_requests": 100,
                "current_unique_query_strings": 150,
                "baseline_unique_query_strings": 50,
            }
        ]

        for semantic_key in ("query_string_cardinality", "uniq_qs"):
            with self.subTest(semantic_key=semantic_key):
                result = self.cache_origin_impact.build_report(
                    self.cache_origin_payload(
                        rows=rows,
                        metric_semantics={semantic_key: "exact_period_unique"},
                    )
                )
                candidate = result["candidates"][0]

                self.assertEqual(candidate["current"]["qs_diversity_ratio"], 1)
                self.assertNotIn(
                    "query_string_cardinality_approximate",
                    candidate.get("confidence_reasons", []),
                )

    def test_cache_origin_marks_missing_optional_metrics_not_evaluated(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                rows=[
                    {
                        "request_path_norm": "/api/search",
                        "bot_class": "bad",
                        "current_requests": 1000,
                        "baseline_requests": 800,
                    }
                ],
                metric_semantics={},
            )
        )
        candidate = result["candidates"][0]
        missing_names = {entry["name"] for entry in candidate["not_evaluated"]}

        self.assertNotIn("cache_misses", candidate["current"])
        self.assertIn("current_miss_rate_pct", missing_names)
        self.assertIn("cache_miss_delta", missing_names)
        self.assertIn("origin_pressure_delta", missing_names)
        self.assertIn("contribution_denominator_absent", candidate["limitations"])
        self.assertNotIn("contribution_withheld_source_limited", candidate["limitations"])
        self.assertNotIn(
            "contribution_withheld_source_limited",
            result["limitations"],
        )

    def test_cache_origin_accepts_supported_dimension_sets(self) -> None:
        cases = [
            (["request_path_norm"], {"request_path_norm": "/api/search"}),
            (
                ["request_path_norm", "bot_class"],
                {"request_path_norm": "/api/search", "bot_class": "bad"},
            ),
            (
                ["request_path_norm", "asn_type"],
                {"request_path_norm": "/api/search", "asn_type": "hosting"},
            ),
            (
                ["request_path_norm", "bot_class", "asn_type"],
                {
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "asn_type": "hosting",
                },
            ),
        ]

        for dimensions, row_dimensions in cases:
            with self.subTest(dimensions=dimensions):
                payload = self.cache_origin_payload(
                    dimensions=dimensions,
                    rows=[
                        {
                            **row_dimensions,
                            "current_requests": 1000,
                            "baseline_requests": 800,
                        }
                    ],
                    metric_semantics={},
                )
                result = self.cache_origin_impact.build_report(payload)
                self.assertEqual(result["dimensions"], dimensions)

    def test_cache_origin_accepts_scoped_host_payloads(self) -> None:
        result = self.cache_origin_impact.build_report(self.cache_origin_payload())

        self.assertEqual(result["scope"]["request_host"], "www.example.com")
        self.assertEqual(result["analysis_type"], "cache_busting_origin_impact")

    def test_cache_origin_accepts_row_level_host_payloads(self) -> None:
        payload = self.cache_origin_payload(
            scope={},
            dimensions=["request_host", "request_path_norm", "asn_type"],
            rows=[
                {
                    "request_host": "www.example.com",
                    "request_path_norm": "/api/search",
                    "asn_type": "hosting",
                    "current_requests": 1000,
                    "baseline_requests": 800,
                }
            ],
            metric_semantics={},
        )

        result = self.cache_origin_impact.build_report(payload)

        self.assertEqual(result["scope"], {})
        self.assertEqual(result["dimensions"][0], "request_host")

    def test_cache_origin_accepts_row_level_host_context_without_host_dimension(self) -> None:
        payload = self.cache_origin_payload(
            scope={},
            dimensions=["request_path_norm", "asn_type"],
            rows=[
                {
                    "request_host": "www.example.com",
                    "request_path_norm": "/api/search",
                    "asn_type": "hosting",
                    "current_requests": 1000,
                    "baseline_requests": 800,
                }
            ],
            metric_semantics={},
        )

        result = self.cache_origin_impact.build_report(payload)

        self.assertEqual(result["scope"], {})
        self.assertEqual(result["dimensions"], ["request_path_norm", "asn_type"])
        self.assertEqual(
            result["candidates"][0]["entity"]["request_host"],
            "www.example.com",
        )

    def test_cache_origin_accepts_scoped_host_for_host_dimension(self) -> None:
        payload = self.cache_origin_payload(
            dimensions=["request_host", "request_path_norm"],
            rows=[
                {
                    "request_path_norm": "/api/search",
                    "current_requests": 1000,
                    "baseline_requests": 800,
                }
            ],
            metric_semantics={},
        )

        result = self.cache_origin_impact.build_report(payload)

        self.assertEqual(result["scope"]["request_host"], "www.example.com")
        self.assertNotIn("request_host", result["candidates"][0]["entity"])

    def test_cache_origin_rejects_missing_host_context(self) -> None:
        payload = self.cache_origin_payload(scope={})

        with self.assertRaisesRegex(ValueError, "Host context"):
            self.cache_origin_impact.build_report(payload)

    def test_cache_origin_rejects_conflicting_scoped_row_host(self) -> None:
        payload = self.cache_origin_payload(
            dimensions=["request_host", "request_path_norm"],
            rows=[
                {
                    "request_host": "api.example.com",
                    "request_path_norm": "/api/search",
                    "current_requests": 1000,
                    "baseline_requests": 800,
                }
            ],
            metric_semantics={},
        )

        with self.assertRaisesRegex(ValueError, "scope.request_host"):
            self.cache_origin_impact.build_report(payload)

    def test_cache_origin_rejects_missing_metric_or_analysis_type(self) -> None:
        payload = self.cache_origin_payload()
        payload.pop("analysis_type")

        with self.assertRaisesRegex(ValueError, "metric or analysis_type"):
            self.cache_origin_impact.build_report(payload)

    def test_cache_origin_rejects_missing_or_malformed_current_window(self) -> None:
        missing = self.cache_origin_payload()
        missing.pop("current_window")
        malformed = self.cache_origin_payload(current_window={"start": "2026-04-18"})
        invalid_timestamp = self.cache_origin_payload(
            current_window={"start": "not-a-date", "end": "2026-04-18T18:00:00Z"}
        )
        reversed_window = self.cache_origin_payload(
            current_window={
                "start": "2026-04-18T18:00:00Z",
                "end": "2026-04-18T12:00:00Z",
            }
        )

        for payload in (missing, malformed, invalid_timestamp, reversed_window):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "current_window"):
                    self.cache_origin_impact.build_report(payload)

    def test_cache_origin_rejects_missing_or_unsupported_dimensions(self) -> None:
        missing = self.cache_origin_payload()
        missing.pop("dimensions")
        empty = self.cache_origin_payload(dimensions=[])
        unsupported_set = self.cache_origin_payload(dimensions=["bot_class"])

        for payload in (missing, empty):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "dimensions"):
                    self.cache_origin_impact.build_report(payload)

        with self.assertRaisesRegex(ValueError, "Unsupported dimensions"):
            self.cache_origin_impact.build_report(unsupported_set)

    def test_cache_origin_rejects_unsupported_non_path_dimensions(self) -> None:
        for dimension in ("client_asn", "resource_category", "hdx_cdn"):
            with self.subTest(dimension=dimension):
                payload = self.cache_origin_payload(
                    dimensions=["request_path_norm", dimension]
                )
                with self.assertRaisesRegex(ValueError, dimension):
                    self.cache_origin_impact.build_report(payload)

    def test_cache_origin_rejects_missing_rows(self) -> None:
        payload = self.cache_origin_payload()
        payload.pop("rows")

        with self.assertRaisesRegex(ValueError, "rows"):
            self.cache_origin_impact.build_report(payload)

    def test_cache_origin_rejects_conflicting_metric_aliases(self) -> None:
        payload = self.cache_origin_payload(
            rows=[
                {
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "current_cnt_all": 1000,
                    "current_requests": 1001,
                }
            ],
            metric_semantics={},
        )

        with self.assertRaisesRegex(ValueError, "conflicting aliases"):
            self.cache_origin_impact.build_report(payload)

    def test_cache_origin_rejects_mixed_period_and_combined_rows(self) -> None:
        payload = self.cache_origin_payload(
            rows=[
                {
                    "period": "current",
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "requests": 1000,
                },
                {
                    "period": "baseline",
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "requests": 800,
                },
                {
                    "request_path_norm": "/api/catalog",
                    "bot_class": "bad",
                    "current_requests": 900,
                    "baseline_requests": 850,
                },
            ],
            metric_semantics={},
        )

        with self.assertRaisesRegex(ValueError, "must not mix period-split rows"):
            self.cache_origin_impact.build_report(payload)

    def test_cache_origin_requires_metric_semantics_for_sensitive_fields(self) -> None:
        payload = self.cache_origin_payload()
        payload.pop("metric_semantics")

        with self.assertRaisesRegex(ValueError, "metric_semantics"):
            self.cache_origin_impact.build_report(payload)

    def test_cache_origin_requires_metric_semantics_for_contribution_denominator(self) -> None:
        payload = self.cache_origin_payload(
            metric_semantics=None,
            rows=[
                {
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "current_requests": 1000,
                    "baseline_requests": 800,
                    "current_total_cache_misses_for_contribution": 1200,
                }
            ],
        )

        with self.assertRaisesRegex(ValueError, "metric_semantics"):
            self.cache_origin_impact.build_report(payload)

    def test_cache_origin_rejects_invalid_numeric_values(self) -> None:
        cases = [
            ("negative", {"current_requests": -1}, "negative"),
            (
                "negative_share_count",
                {"current_total_cache_misses_for_share": -1},
                "negative",
            ),
            ("non_numeric", {"current_requests": "many"}, "numeric"),
            ("nan_string", {"current_requests": "NaN"}, "numeric"),
            ("infinite_string", {"current_requests": "Infinity"}, "numeric"),
            ("bad_pct", {"cache_miss_contribution_pct": 101}, "percentage"),
        ]

        for label, fields, message in cases:
            with self.subTest(label=label):
                payload = self.cache_origin_payload(
                    rows=[
                        {
                            "request_path_norm": "/api/search",
                            "bot_class": "bad",
                            "baseline_requests": 800,
                            **fields,
                        }
                    ]
                )
                with self.assertRaisesRegex(ValueError, message):
                    self.cache_origin_impact.build_report(payload)

    def test_cache_origin_ignores_standalone_trusted_context(self) -> None:
        payload = self.cache_origin_payload(
            rows=[
                {
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "current_requests": 1000,
                    "baseline_requests": 1000,
                    "current_unique_query_strings": 700,
                    "baseline_unique_query_strings": 300,
                    "cache_miss_contribution_pct": 25,
                }
            ],
            trusted_context={"direct_mcp_trusted_context": True}
        )

        result = self.cache_origin_impact.build_report(payload)

        self.assertLessEqual(
            {"low": 0, "medium": 1, "high": 2}[result["confidence"]],
            {"low": 0, "medium": 1, "high": 2}["medium"],
        )
        self.assertEqual(result["confidence"], "medium")
        self.assertIn(
            "caller_supplied_json_confidence_cap",
            result["confidence_reasons"],
        )
        self.assertNotIn("direct_mcp_trusted_context", result["confidence_reasons"])

    def test_cache_origin_trusted_in_process_context_can_raise_confidence(self) -> None:
        payload = self.cache_origin_payload(
            baseline_windows=[
                {
                    "start": "2026-04-18T06:00:00Z",
                    "end": "2026-04-18T12:00:00Z",
                }
            ],
            metric_semantics={
                "unique_query_strings": "exact_period_unique",
                "origin_p95_ms": "metadata_merged_quantile",
                "contribution_fields": "complete_scope_pre_limit",
            },
            rows=[
                {
                    "request_path_norm": "/api/search",
                    "bot_class": "bad",
                    "current_requests": 1500,
                    "baseline_requests": 1400,
                    "current_cache_misses": 500,
                    "baseline_cache_misses": 300,
                    "current_unique_query_strings": 900,
                    "baseline_unique_query_strings": 500,
                    "current_origin_p95_ms": 300,
                    "baseline_origin_p95_ms": 200,
                    "cache_miss_contribution_pct": 20,
                    "origin_pressure_contribution_pct": 25,
                }
            ],
        )
        trusted_context = {
            "direct_mcp_trusted_context": True,
            "table_metadata": {"name": "bot_agg_path_hour"},
            "retained_dimensions": ["request_path_norm", "bot_class"],
            "query_digest": "query-sha256",
            "result_digest": "result-sha256",
            "comparable_windows": True,
            "current_count_sufficient": True,
            "baseline_count_sufficient": True,
            "complete_scope_contribution": True,
        }

        result = self.cache_origin_impact.build_report(
            payload,
            trusted_context=trusted_context,
        )
        candidate = result["candidates"][0]

        self.assertEqual(result["confidence"], "high")
        self.assertEqual(candidate["confidence"], "high")
        self.assertIn("direct_mcp_trusted_context", candidate["confidence_reasons"])

    def test_cache_origin_sparse_counts_lower_confidence(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                rows=[
                    {
                        "request_path_norm": "/small",
                        "bot_class": "bad",
                        "current_requests": 50,
                        "baseline_requests": 40,
                        "current_cache_misses": 5,
                        "baseline_cache_misses": 4,
                        "current_unique_query_strings": 25,
                        "baseline_unique_query_strings": 20,
                    }
                ],
            )
        )
        candidate = result["candidates"][0]

        self.assertEqual(candidate["confidence"], "low")
        self.assertIn("sparse_counts", candidate["confidence_reasons"])

    def test_cache_origin_scores_features_bands_and_full_report_shape(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                baseline_windows=[
                    {
                        "start": "2026-04-18T06:00:00Z",
                        "end": "2026-04-18T12:00:00Z",
                    }
                ],
                metric_semantics={
                    "unique_query_strings": "exact_period_unique",
                    "origin_p95_ms": "metadata_merged_quantile",
                    "contribution_fields": "complete_scope_pre_limit",
                },
                rows=[
                    {
                        "request_path_norm": "/api/search",
                        "bot_class": "bad",
                        "current_requests": 10000,
                        "baseline_requests": 10000,
                        "current_cache_misses": 9000,
                        "baseline_cache_misses": 7000,
                        "current_unique_query_strings": 8000,
                        "baseline_unique_query_strings": 4000,
                        "current_origin_p95_ms": 300,
                        "baseline_origin_p95_ms": 100,
                        "origin_pressure_contribution_pct": 10,
                        "bot_miss_share_pct": 50,
                    }
                ],
            )
        )
        candidate = result["candidates"][0]
        feature_names = self.cache_origin_feature_names(candidate)

        for key in (
            "schema_version",
            "analysis_type",
            "source_skill",
            "comparison_type",
            "granularity",
            "table_used",
            "summary_table_used",
            "scope",
            "current_window",
            "baseline_windows",
            "baseline_normalization",
            "metric_semantics",
            "candidates",
            "not_evaluated",
            "interpretation_constraints",
        ):
            self.assertIn(key, result)
        self.assertEqual(result["schema_version"], "cache_origin_impact_report.v1")
        self.assertEqual(candidate["candidate_score"], 100)
        self.assertEqual(candidate["candidate_band"], "high")
        self.assertEqual(sum(feature["points"] for feature in candidate["features"]), 105)
        self.assertIn("high_query_string_diversity", feature_names)
        self.assertIn("query_string_diversity_increased", feature_names)
        self.assertIn("high_miss_rate", feature_names)
        self.assertIn("miss_rate_increased", feature_names)
        self.assertIn("origin_tail_latency_increased", feature_names)
        self.assertIn("origin_pressure_contributor", feature_names)
        self.assertIn("bot_attributable_majority", feature_names)
        self.assertIn("large_current_volume", feature_names)
        self.assertEqual(
            set(candidate["finding_types"]),
            {
                "cache_busting_candidate",
                "cache_miss_movement_candidate",
                "origin_impact_candidate",
                "bot_attributable_cache_misses",
            },
        )

    def test_cache_origin_score_band_boundaries_and_high_miss_rate_threshold(self) -> None:
        rows = [
            {
                "request_path_norm": "/high",
                "bot_class": "bad",
                "current_requests": 10000,
                "baseline_requests": 10000,
                "current_cache_misses": 9000,
                "baseline_cache_misses": 7000,
                "current_unique_query_strings": 8000,
                "baseline_unique_query_strings": 4000,
                "current_origin_p95_ms": 300,
                "baseline_origin_p95_ms": 100,
                "origin_pressure_contribution_pct": 10,
                "bot_miss_share_pct": 50,
            },
            {
                "request_path_norm": "/medium",
                "bot_class": "bad",
                "current_requests": 1000,
                "baseline_requests": 1000,
                "current_cache_misses": 800,
                "baseline_cache_misses": 700,
                "current_unique_query_strings": 500,
                "baseline_unique_query_strings": 250,
            },
            {
                "request_path_norm": "/low",
                "bot_class": "bad",
                "current_requests": 1000,
                "baseline_requests": 1000,
                "current_cache_misses": 799,
                "baseline_cache_misses": 799,
                "current_unique_query_strings": 800,
                "baseline_unique_query_strings": 800,
            },
            {
                "request_path_norm": "/info",
                "bot_class": "bad",
                "current_requests": 1000,
                "baseline_requests": 1000,
                "current_cache_misses": 100,
                "baseline_cache_misses": 100,
            },
        ]

        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                metric_semantics={
                    "unique_query_strings": "exact_period_unique",
                    "origin_p95_ms": "metadata_merged_quantile",
                    "contribution_fields": "complete_scope_pre_limit",
                },
                rows=rows,
            )
        )
        by_path = {
            candidate["entity"]["request_path_norm"]: candidate
            for candidate in result["candidates"]
        }

        self.assertEqual(by_path["/high"]["candidate_band"], "high")
        self.assertEqual(by_path["/medium"]["candidate_band"], "medium")
        self.assertEqual(by_path["/low"]["candidate_band"], "low")
        self.assertEqual(by_path["/info"]["candidate_band"], "informational")
        self.assertIn(
            "high_miss_rate",
            self.cache_origin_feature_names(by_path["/medium"]),
        )
        self.assertNotIn(
            "high_miss_rate",
            self.cache_origin_feature_names(by_path["/low"]),
        )

    def test_cache_origin_detector_guards_at_thresholds(self) -> None:
        cases = [
            (
                "sparse_current_volume",
                {
                    "current_requests": 999,
                    "current_cache_misses": 99,
                    "current_unique_query_strings": 900,
                },
                set(),
            ),
            (
                "query_string_cardinality_below_100",
                {
                    "current_requests": 1000,
                    "current_cache_misses": 100,
                    "current_unique_query_strings": 99,
                },
                {"cache_miss_movement_candidate"},
            ),
            (
                "query_string_ratio_below_half",
                {
                    "current_requests": 1000,
                    "current_cache_misses": 100,
                    "current_unique_query_strings": 499,
                },
                {"cache_miss_movement_candidate"},
            ),
            (
                "query_string_at_threshold",
                {
                    "current_requests": 1000,
                    "current_cache_misses": 100,
                    "current_unique_query_strings": 500,
                },
                {"cache_busting_candidate", "cache_miss_movement_candidate"},
            ),
            (
                "cache_misses_below_100",
                {
                    "current_requests": 1000,
                    "current_cache_misses": 99,
                    "current_unique_query_strings": 500,
                },
                {"cache_busting_candidate"},
            ),
            (
                "cache_misses_at_100",
                {
                    "current_requests": 1000,
                    "current_cache_misses": 100,
                    "current_unique_query_strings": 500,
                },
                {"cache_busting_candidate", "cache_miss_movement_candidate"},
            ),
            (
                "origin_missing",
                {"current_requests": 1000, "current_cache_misses": 100},
                {"cache_miss_movement_candidate"},
            ),
            (
                "origin_zero",
                {
                    "current_requests": 1000,
                    "current_cache_misses": 100,
                    "current_origin_p95_ms": 0,
                },
                {"cache_miss_movement_candidate"},
            ),
            (
                "origin_at_threshold",
                {
                    "current_requests": 1000,
                    "current_cache_misses": 100,
                    "current_origin_p95_ms": 1,
                },
                {"cache_miss_movement_candidate", "origin_impact_candidate"},
            ),
            (
                "bot_share_absent",
                {"current_requests": 1000, "current_cache_misses": 100},
                {"cache_miss_movement_candidate"},
            ),
            (
                "bot_share_below_25",
                {
                    "current_requests": 1000,
                    "current_cache_misses": 100,
                    "bot_miss_share_pct": 24.9,
                },
                {"cache_miss_movement_candidate"},
            ),
            (
                "bot_share_at_25",
                {
                    "current_requests": 1000,
                    "current_cache_misses": 100,
                    "bot_miss_share_pct": 25,
                },
                {"cache_miss_movement_candidate", "bot_attributable_cache_misses"},
            ),
        ]

        for label, fields, expected in cases:
            with self.subTest(label=label):
                payload = self.cache_origin_payload(
                    metric_semantics={
                        "unique_query_strings": "exact_period_unique",
                        "origin_p95_ms": "metadata_merged_quantile",
                    },
                    rows=[
                        {
                            "request_path_norm": f"/{label}",
                            "bot_class": "bad",
                            **fields,
                        }
                    ],
                )
                candidate = self.cache_origin_impact.build_report(payload)[
                    "candidates"
                ][0]
                self.assertEqual(set(candidate["finding_types"]), expected)

    def test_cache_origin_ranks_volume_sufficient_before_sparse_high_score(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                metric_semantics={"unique_query_strings": "exact_period_unique"},
                rows=[
                    {
                        "request_path_norm": "/sparse-high-score",
                        "bot_class": "bad",
                        "current_requests": 50,
                        "baseline_requests": 50,
                        "current_cache_misses": 45,
                        "baseline_cache_misses": 10,
                        "current_unique_query_strings": 50,
                        "baseline_unique_query_strings": 10,
                    },
                    {
                        "request_path_norm": "/volume-sufficient",
                        "bot_class": "bad",
                        "current_requests": 1000,
                        "baseline_requests": 1000,
                        "current_cache_misses": 100,
                        "baseline_cache_misses": 100,
                    },
                ],
            ),
            limit=1,
        )

        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(
            result["candidates"][0]["entity"]["request_path_norm"],
            "/volume-sufficient",
        )
        self.assertNotIn("sparse_counts", result["confidence_reasons"])

    def test_cache_origin_withholds_source_limited_contribution_fields(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                partial_current_bucket=True,
                metric_semantics={
                    "origin_p95_ms": "metadata_merged_quantile",
                    "contribution_fields": "source_limited",
                },
                rows=[
                    {
                        "request_path_norm": "/api/search",
                        "bot_class": "bad",
                        "current_requests": 1000,
                        "current_cache_misses": 500,
                        "current_origin_p95_ms": 200,
                        "cache_miss_contribution_pct": 50,
                        "origin_pressure_contribution_pct": 40,
                    }
                ],
            )
        )
        candidate = result["candidates"][0]
        withheld = {
            entry["name"]: entry["reason"]
            for entry in candidate["not_evaluated"]
            if entry["reason"] == "contribution_withheld_source_limited"
        }

        self.assertNotIn("cache_miss_contribution_pct", candidate["deltas"])
        self.assertNotIn("origin_pressure_contribution_pct", candidate["deltas"])
        self.assertEqual(
            withheld,
            {
                "cache_miss_contribution_pct": "contribution_withheld_source_limited",
                "origin_pressure_contribution_pct": "contribution_withheld_source_limited",
            },
        )
        self.assertIn(
            "contribution_withheld_source_limited",
            candidate["confidence_reasons"],
        )
        self.assertIn("partial_current_bucket", candidate["confidence_reasons"])
        self.assertIn("contribution_withheld_source_limited", candidate["limitations"])
        self.assertIn("partial_current_bucket", candidate["limitations"])
        self.assertEqual(candidate["confidence"], "low")

    def test_cache_origin_source_limited_contribution_absence_lowers_confidence(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                metric_semantics={
                    "origin_p95_ms": "metadata_merged_quantile",
                    "contribution_fields": "source_limited",
                },
                rows=[
                    {
                        "request_path_norm": "/api/search",
                        "bot_class": "bad",
                        "current_requests": 1000,
                        "baseline_requests": 1000,
                        "current_cache_misses": 100,
                        "baseline_cache_misses": 100,
                        "current_origin_p95_ms": 100,
                        "baseline_origin_p95_ms": 100,
                    }
                ],
            )
        )
        candidate = result["candidates"][0]

        self.assertEqual(candidate["confidence"], "low")
        self.assertIn(
            "contribution_withheld_source_limited",
            candidate["limitations"],
        )
        self.assertEqual(result["confidence"], "low")

    def test_cache_origin_rowset_complete_contributions_are_computed_before_limit(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                rowset_complete=True,
                metric_semantics={"origin_p95_ms": "metadata_merged_quantile"},
                rows=[
                    {
                        "request_path_norm": "/dominant",
                        "bot_class": "bad",
                        "current_requests": 1000,
                        "current_cache_misses": 900,
                        "current_origin_p95_ms": 200,
                    },
                    {
                        "request_path_norm": "/small",
                        "bot_class": "bad",
                        "current_requests": 1000,
                        "current_cache_misses": 100,
                        "current_origin_p95_ms": 200,
                    },
                ],
            ),
            limit=1,
        )
        candidate = result["candidates"][0]

        self.assertEqual(candidate["entity"]["request_path_norm"], "/dominant")
        self.assertEqual(candidate["deltas"]["cache_miss_contribution_pct"], 90)
        self.assertEqual(candidate["deltas"]["origin_pressure_contribution_pct"], 90)
        self.assertEqual(
            candidate["share_denominators"]["current_total_cache_misses_for_contribution"],
            1000,
        )
        self.assertEqual(
            candidate["share_denominators"]["current_total_origin_pressure_score"],
            200,
        )
        self.assertIn(
            "origin_pressure_contributor",
            self.cache_origin_feature_names(candidate),
        )

    def test_cache_origin_period_split_rows_preserve_current_contribution_fields(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                baseline_windows=[
                    {
                        "start": "2026-04-18T06:00:00Z",
                        "end": "2026-04-18T12:00:00Z",
                    }
                ],
                metric_semantics={
                    "origin_p95_ms": "metadata_merged_quantile",
                    "contribution_fields": "complete_scope_pre_limit",
                },
                rows=[
                    {
                        "period": "current",
                        "request_path_norm": "/a",
                        "bot_class": "bad",
                        "requests": 1000,
                        "cache_misses": 100,
                        "origin_p95_ms": 100,
                        "origin_pressure_contribution_pct": 5,
                    },
                    {
                        "period": "baseline",
                        "request_path_norm": "/a",
                        "bot_class": "bad",
                        "requests": 1000,
                        "cache_misses": 100,
                        "origin_p95_ms": 100,
                    },
                    {
                        "period": "current",
                        "request_path_norm": "/b",
                        "bot_class": "bad",
                        "requests": 1000,
                        "cache_misses": 200,
                        "origin_p95_ms": 200,
                        "origin_pressure_contribution_pct": 20,
                    },
                    {
                        "period": "baseline",
                        "request_path_norm": "/b",
                        "bot_class": "bad",
                        "requests": 1000,
                        "cache_misses": 100,
                        "origin_p95_ms": 100,
                    },
                ],
            )
        )
        by_path = {
            candidate["entity"]["request_path_norm"]: candidate
            for candidate in result["candidates"]
        }
        candidate = by_path["/b"]

        self.assertEqual(candidate["deltas"]["origin_pressure_contribution_pct"], 20)
        self.assertIn(
            "origin_pressure_contributor",
            self.cache_origin_feature_names(candidate),
        )

    def test_cache_origin_computes_multiple_selected_bot_class_shares(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                scope={
                    "request_host": "www.example.com",
                    "selected_bot_classes": ["bad", "unknown"],
                },
                metric_semantics={"origin_p95_ms": "metadata_merged_quantile"},
                rows=[
                    {
                        "request_path_norm": "/api/search",
                        "bot_class": "bad",
                        "current_requests": 1000,
                        "current_cache_misses": 100,
                        "current_origin_p95_ms": 200,
                        "current_total_cache_misses_for_share": 200,
                        "current_selected_bot_class_cache_misses_for_share": 150,
                        "current_total_origin_pressure_for_path": 100,
                        "current_selected_bot_class_origin_pressure_for_path": 80,
                    }
                ],
            )
        )
        candidate = result["candidates"][0]

        self.assertEqual(candidate["current"]["bot_miss_share_pct"], 75)
        self.assertEqual(candidate["current"]["bot_origin_pressure_share_pct"], 80)
        self.assertEqual(
            candidate["share_denominators"]["selected_bot_classes"],
            ["bad", "unknown"],
        )
        self.assertIn("bot_attributable_cache_misses", candidate["finding_types"])
        self.assertIn("bot_attributable_origin_pressure", candidate["finding_types"])

    def test_cache_origin_current_only_screening_keeps_present_evidence(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                comparison_type="current_only",
                baseline_windows=[],
                metric_semantics={
                    "unique_query_strings": "exact_period_unique",
                    "origin_p95_ms": "metadata_merged_quantile",
                },
                rows=[
                    {
                        "request_path_norm": "/api/search",
                        "bot_class": "bad",
                        "current_requests": 1000,
                        "current_cache_misses": 100,
                        "current_unique_query_strings": 500,
                        "current_origin_p95_ms": 200,
                    }
                ],
            )
        )
        candidate = result["candidates"][0]
        not_evaluated_reasons = {
            entry["reason"] for entry in candidate["not_evaluated"]
        }

        self.assertEqual(candidate["baseline"], {})
        self.assertIn("cache_busting_candidate", candidate["finding_types"])
        self.assertIn("cache_miss_movement_candidate", candidate["finding_types"])
        self.assertIn("origin_impact_candidate", candidate["finding_types"])
        self.assertIn("baseline_absent", not_evaluated_reasons)

    def test_cache_origin_optional_response_bytes_do_not_affect_score(self) -> None:
        base_row = {
            "request_path_norm": "/api/search",
            "bot_class": "bad",
            "current_requests": 1500,
            "baseline_requests": 1500,
            "current_cache_misses": 600,
            "baseline_cache_misses": 300,
            "current_unique_query_strings": 900,
            "baseline_unique_query_strings": 450,
        }
        without_bytes = self.cache_origin_impact.build_report(
            self.cache_origin_payload(rows=[base_row])
        )["candidates"][0]
        with_bytes = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                rows=[{**base_row, "current_response_bytes": 4096}]
            )
        )["candidates"][0]

        self.assertEqual(with_bytes["candidate_score"], without_bytes["candidate_score"])
        self.assertFalse(without_bytes["optional_metadata"]["response_bytes"]["available"])
        self.assertEqual(
            with_bytes["optional_metadata"]["response_bytes"],
            {"available": True, "current": 4096},
        )
        self.assertIn(
            "response_byte_metadata_not_available",
            without_bytes["limitations"],
        )

    def test_cache_origin_bot_summary_context_is_host_scope_metadata(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                bot_summary_context={
                    "scope": {"request_host": "www.example.com"},
                    "metrics": {
                        "host_bot_traffic_share_pct": 42.1,
                        "host_ai_category_share_pct": 7.4,
                    },
                }
            )
        )
        candidate = result["candidates"][0]
        context = candidate["optional_metadata"]["bot_summary_context"]

        self.assertEqual(len(result["candidates"]), 1)
        self.assertTrue(context["available"])
        self.assertIn(
            "host_scope_context_not_path_level_evidence",
            context["limitations"],
        )
        self.assertIn(
            "host_scope_context_not_path_level_evidence",
            candidate["limitations"],
        )

    def test_cache_origin_end_to_end_path_summary_fixture_shape(self) -> None:
        result = self.cache_origin_impact.build_report(self.cache_origin_e2e_payload())
        candidate = result["candidates"][0]
        feature_names = self.cache_origin_feature_names(candidate)

        self.assertEqual(result["schema_version"], "cache_origin_impact_report.v1")
        self.assertEqual(candidate["candidate_band"], "high")
        self.assertEqual(candidate["confidence"], "medium")
        self.assertEqual(result["confidence"], "medium")
        self.assertNotIn("direct_mcp_trusted_context", result["confidence_reasons"])
        self.assertEqual(
            result["metric_semantics"]["origin_pressure_score"],
            "proxy_misses_times_origin_p95_seconds",
        )
        self.assertEqual(result["metric_semantics"]["uniq_qs"], "exact_period_unique")
        self.assertEqual(candidate["current"]["unique_query_strings"], 8500)
        self.assertEqual(candidate["baseline"]["unique_query_strings"], 4500)
        self.assertIn("high_query_string_diversity", feature_names)
        self.assertIn("query_string_diversity_increased", feature_names)
        self.assertIn("origin_pressure_contributor", feature_names)
        self.assertIn("bot_attributable_majority", feature_names)
        self.assertEqual(
            candidate["share_denominators"]["cache_miss_contribution_basis"],
            "complete_scope_pre_limit",
        )
        self.assertEqual(
            candidate["share_denominators"]["origin_pressure_contribution_basis"],
            "complete_scope_pre_limit",
        )
        self.assertEqual(
            candidate["share_denominators"]["selected_bot_classes"],
            ["bad", "unknown"],
        )
        self.assertEqual(candidate["deltas"]["cache_miss_contribution_pct"], 45)
        self.assertEqual(candidate["deltas"]["origin_pressure_contribution_pct"], 18)
        self.assertIn(
            "host_scope_context_not_path_level_evidence",
            candidate["optional_metadata"]["bot_summary_context"]["limitations"],
        )
        self.assertIn(
            "mechanical_candidate_only",
            result["interpretation_constraints"],
        )

    def test_cache_origin_cli_reads_stdin_json(self) -> None:
        completed = self.run_cache_origin_cli(
            input_text=json.dumps(self.cache_origin_e2e_payload())
        )
        result = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(result["schema_version"], "cache_origin_impact_report.v1")

    def test_cache_origin_cli_reads_file_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload_path = Path(directory) / "cache-origin-input.json"
            payload_path.write_text(
                json.dumps(self.cache_origin_e2e_payload()),
                encoding="utf-8",
            )

            completed = self.run_cache_origin_cli(["--file", str(payload_path)])

        result = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(result["schema_version"], "cache_origin_impact_report.v1")

    def test_cache_origin_cli_reads_positional_json(self) -> None:
        completed = self.run_cache_origin_cli(
            [json.dumps(self.cache_origin_e2e_payload())]
        )
        result = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(result["schema_version"], "cache_origin_impact_report.v1")

    def test_cache_origin_cli_limit_preserves_complete_scope_denominators(self) -> None:
        payload = self.cache_origin_payload(
            rowset_complete=True,
            metric_semantics={"origin_p95_ms": "metadata_merged_quantile"},
            rows=[
                {
                    "request_path_norm": "/dominant",
                    "bot_class": "bad",
                    "current_requests": 1000,
                    "current_cache_misses": 900,
                    "current_origin_p95_ms": 200,
                },
                {
                    "request_path_norm": "/small",
                    "bot_class": "bad",
                    "current_requests": 1000,
                    "current_cache_misses": 100,
                    "current_origin_p95_ms": 200,
                },
            ],
        )

        completed = self.run_cache_origin_cli(
            ["--limit", "1"],
            input_text=json.dumps(payload),
        )
        result = json.loads(completed.stdout)
        candidate = result["candidates"][0]

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(candidate["entity"]["request_path_norm"], "/dominant")
        self.assertEqual(
            candidate["share_denominators"]["current_total_cache_misses_for_contribution"],
            1000,
        )
        self.assertEqual(candidate["deltas"]["cache_miss_contribution_pct"], 90)

    def test_cache_origin_cli_invalid_input_reports_error(self) -> None:
        completed = self.run_cache_origin_cli(input_text="{}")

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertTrue(completed.stderr.startswith("ERROR: "))
        self.assertIn("metric or analysis_type", completed.stderr)

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


if __name__ == "__main__":
    unittest.main()
