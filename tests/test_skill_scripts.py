from __future__ import annotations

import copy
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


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
        cls.attribution = load_module(
            "attribution",
            ROOT / "skills/bot-insights/scripts/attribution.py",
        )
        cls.scorecard = load_module(
            "scorecard",
            ROOT / "skills/bot-insights/scripts/scorecard.py",
        )
        cls.cache_origin_impact = load_module(
            "cache_origin_impact",
            ROOT / "skills/bot-insights/scripts/cache_origin_impact.py",
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

    def assert_invalid_input_code(self, exc: Exception, code: str) -> None:
        self.assertIsInstance(exc, self.attribution.InvalidInputError)
        self.assertEqual(exc.document["schema_version"], "bot_attribution_error.v1")
        self.assertEqual(exc.document["error_type"], "invalid_input")
        self.assertEqual(exc.document["errors"][0]["code"], code)

    def trusted_attribution_input(self, rows=None):
        return {
            "metric": "requests",
            "dimensions": ["client_asn"],
            "table_used": "bot_summary_day",
            "summary_table_used": True,
            "scope": {"request_host": "www.example.com"},
            "current_window": {
                "start": "2026-04-01T00:00:00Z",
                "end": "2026-04-02T00:00:00Z",
            },
            "baseline_windows": [
                {
                    "start": "2026-03-01T00:00:00Z",
                    "end": "2026-03-02T00:00:00Z",
                    "label": "previous_day",
                }
            ],
            "baseline_method": "single_previous_window",
            "baseline_value_semantic": "duration_normalized_to_current_window",
            "metadata": {
                "generator_name": "bot-insights-attribution-sql",
                "generator_version": "1.0.0",
                "template_id": "full_scope_joined_pre_limit_v1",
                "query_fingerprint": "sha256:" + "1" * 64,
                "selected_table": "bot_summary_day",
                "selected_columns": ["timestamp", "client_asn", "request_host", "sum(cnt_all)"],
                "metadata_origin": "direct_hydrolix_table_metadata",
                "metadata_fingerprint": "sha256:" + "2" * 64,
                "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:2026-04-02T00:00:00Z",
                "merge_expressions": {"sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"},
                "baseline_normalization": {
                    "method": "scale_baseline_to_current_window_duration",
                    "current_duration_seconds": 86400,
                    "baseline_duration_seconds": 86400,
                    "factor": 1,
                    "factor_expression": "current_duration_seconds / baseline_duration_seconds",
                    "applies_to": ["baseline"],
                },
                "limit_stage": "after_denominator",
            },
            "rows": rows
            if rows is not None
            else [
                {
                    "client_asn": "64500",
                    "current_requests": 180,
                    "baseline_raw_requests": 100,
                    "baseline_requests": 100,
                    "current_support_raw": 180,
                    "baseline_support_raw": 100,
                    "absolute_delta": 80,
                    "complete_scope_total_abs_delta": 80,
                    "contribution_pct": 100,
                }
            ],
        }

    def trusted_context_for(self, input_doc, *, evidence=None, result_digest=None):
        digest = result_digest or self.attribution.result_digest_v1(input_doc)
        metadata = input_doc["metadata"]
        base_evidence = {
            "evidence_id": "complete-scope-pre-limit-v1",
            "evidence_type": "complete_scope_pre_limit_evidence",
            "applies_to": {"scope": "report"},
            "evidence_source": "trusted_template_generator",
            "generator_name": "bot-insights-attribution-sql",
            "generator_version": "1.0.0",
            "template_id": metadata["template_id"],
            "query_fingerprint": metadata["query_fingerprint"],
            "result_digest": digest,
            "metric": "requests",
            "metric_semantics_reviewed": True,
            "dimensions": ["client_asn"],
            "grouped_dimensions": ["client_asn"],
            "selected_table": metadata["selected_table"],
            "selected_columns": metadata["selected_columns"],
            "metadata_origin": metadata["metadata_origin"],
            "metadata_fingerprint": metadata["metadata_fingerprint"],
            "metadata_retrieval_identity": metadata["metadata_retrieval_identity"],
            "merge_expressions": metadata["merge_expressions"],
            "scope": input_doc["scope"],
            "current_window": input_doc["current_window"],
            "baseline_windows": input_doc["baseline_windows"],
            "baseline_method": input_doc["baseline_method"],
            "baseline_value_semantic": input_doc["baseline_value_semantic"],
            "baseline_normalization": metadata["baseline_normalization"],
            "scope_matches_report": True,
            "windows_match_report": True,
            "baseline_method_matches_report": True,
            "denominator_scope_matches_report": True,
            "denominator_expression": "sum(abs(current_requests - baseline_requests)) over ()",
            "denominator_basis": "sum_abs_delta",
            "computed_over_complete_grouped_scope": True,
            "computed_before_output_limit": True,
            "source_limit_applied_before_denominator": False,
            "limit_stage": "after_denominator",
        }
        return {
            "trusted_generator_invocation": True,
            "generator_name": "bot-insights-attribution-sql",
            "generator_version": "1.0.0",
            "wrapper_name": "bot-insights-attribution-runner",
            "wrapper_version": "1.0.0",
            "template_id": metadata["template_id"],
            "query_fingerprint": metadata["query_fingerprint"],
            "result_digest": digest,
            "result_origin": "direct_mcp_tool_output",
            "metadata_origin": metadata["metadata_origin"],
            "selected_table": metadata["selected_table"],
            "selected_columns": metadata["selected_columns"],
            "metadata_fingerprint": metadata["metadata_fingerprint"],
            "metadata_retrieval_identity": metadata["metadata_retrieval_identity"],
            "merge_expressions": metadata["merge_expressions"],
            "trusted_evidence": evidence if evidence is not None else [base_evidence],
        }

    def provided_contribution_context_for(self, input_doc):
        context = self.trusted_context_for(input_doc)
        context["trusted_evidence"][0].update(
            {
                "evidence_id": "provided-contribution-single-dimension-v1",
                "evidence_type": "provided_contribution_evidence",
                "reviewed_metric_kind": "additive_count",
                "contribution_pct_field": "contribution_pct",
                "denominator_field": "complete_scope_total_abs_delta",
                "pre_denominator_filter_applied": False,
                "per_row_contribution": True,
                "contribution_pct_tolerance_pp": 0.01,
            }
        )
        return context

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

    def test_posture_ignores_non_finite_numeric_values(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "previous_window",
                "granularity": "hour",
                "table_used": "bot_summary_hour",
                "current": {"requests": float("nan")},
                "baseline": {"requests": 1},
            }
        )

        self.assertEqual(result["metrics"], [])
        json.dumps(result, allow_nan=False)

    def test_posture_sanitizes_non_finite_metadata_values(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": float("nan"),
                "granularity": float("inf"),
                "table_used": float("-inf"),
                "scope": {"request_host": "www.example.com", "sample_rate": float("nan")},
                "current_window": {"start": "2026-04-01", "weight": float("inf")},
                "baseline_windows": [
                    {
                        "start": "2026-03-25",
                        "end": "2026-04-01",
                        "weight": float("-inf"),
                    }
                ],
                "current": {"requests": 10},
                "baseline": {"requests": 5},
            }
        )

        self.assertIsNone(result["comparison_type"])
        self.assertIsNone(result["granularity"])
        self.assertIsNone(result["table_used"])
        self.assertIsNone(result["scope"]["sample_rate"])
        self.assertIsNone(result["current_window"]["weight"])
        self.assertIsNone(result["baseline_windows"][0]["weight"])
        json.dumps(result, allow_nan=False)

    def test_compare_posture_main_sanitizes_non_finite_scalar_metadata(self) -> None:
        payload = json.dumps(
            {
                "comparison_type": float("nan"),
                "granularity": float("inf"),
                "table_used": float("-inf"),
                "current": {"requests": 10},
                "baseline": {"requests": 5},
            }
        )
        with mock.patch("sys.argv", ["compare_posture.py", payload]):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                exit_code = self.compare_posture.main()

        document = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertIsNone(document["comparison_type"])
        self.assertIsNone(document["granularity"])
        self.assertIsNone(document["table_used"])
        self.assertEqual(stderr.getvalue(), "")

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

    def test_mover_uses_absolute_delta_denominator_for_unqualified_total_delta(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "week_over_week",
                "granularity": "day",
                "table_used": "bot_summary_day",
                "dimension": "client_asn",
                "metric": "requests",
                "total_delta": 250,
                "movers": [
                    {"value": "64500", "current": 420, "baseline": 80},
                    {"value": "64600", "current": 260, "baseline": 210},
                ],
            },
            schema="movers",
        )

        self.assertEqual(result["total_delta"], 390)
        self.assertEqual(result["total_delta_basis"], "sum_abs_mover_delta")
        self.assertEqual(result["movers"][0]["contribution_pct"], 87.179487)
        self.assertEqual(result["movers"][1]["contribution_pct"], 12.820513)

    def test_mover_uses_qualified_complete_scope_total_abs_delta_denominator(
        self,
    ) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "week_over_week",
                "granularity": "day",
                "table_used": "bot_summary_day",
                "dimension": "client_asn",
                "metric": "requests",
                "total_delta": 500,
                "total_delta_basis": "complete_scope_total_abs_delta",
                "movers": [
                    {"value": "64500", "current": 300, "baseline": 200},
                    {"value": "64600", "current": 250, "baseline": 200},
                ],
            },
            schema="movers",
        )

        self.assertEqual(result["total_delta"], 500)
        self.assertEqual(
            result["total_delta_basis"], "complete_scope_total_abs_delta"
        )
        self.assertEqual(result["movers"][0]["contribution_pct"], 20)
        self.assertEqual(result["movers"][1]["contribution_pct"], 10)

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

    def test_mover_sanitizes_non_finite_metadata_and_values(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": float("nan"),
                "granularity": float("inf"),
                "table_used": float("-inf"),
                "dimension": float("nan"),
                "metric": float("inf"),
                "scope": {"request_host": "www.example.com", "sample_rate": float("nan")},
                "current_window": {"start": "2026-04-01", "weight": float("inf")},
                "baseline_windows": [
                    {
                        "start": "2026-03-25",
                        "end": "2026-04-01",
                        "weight": float("-inf"),
                    }
                ],
                "movers": [
                    {"value": float("nan"), "current": 300, "baseline": 200},
                ],
            },
            schema="movers",
        )

        self.assertIsNone(result["comparison_type"])
        self.assertIsNone(result["granularity"])
        self.assertIsNone(result["table_used"])
        self.assertIsNone(result["dimension"])
        self.assertIsNone(result["metric"])
        self.assertIsNone(result["scope"]["sample_rate"])
        self.assertIsNone(result["current_window"]["weight"])
        self.assertIsNone(result["baseline_windows"][0]["weight"])
        self.assertIsNone(result["movers"][0]["value"])
        json.dumps(result, allow_nan=False)

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

    def test_control_review_sanitizes_non_finite_metadata(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "post_change_vs_expected",
                "change_time": float("nan"),
                "table_used": float("inf"),
                "target": {"policy_id": "policy-123", "sample_rate": float("nan")},
                "scope": {"request_host": "www.example.com", "sample_rate": float("inf")},
                "before_window": {"start": "2026-03-25", "weight": float("-inf")},
                "after_window": {"start": "2026-04-01", "weight": float("inf")},
                "expected_window": {"start": "2026-03-25", "weight": float("nan")},
                "collateral_checks": [{"name": "volume", "weight": float("nan")}],
                "displacement_checks": [{"name": "path_shift", "weight": float("inf")}],
                "before": {"siem_blocked_requests": 90},
                "after": {"siem_blocked_requests": 130},
                "expected": {"siem_blocked_requests": 100},
                "target_metrics": ["siem_blocked_requests"],
            }
        )

        self.assertIsNone(result["change_time"])
        self.assertIsNone(result["table_used"])
        self.assertIsNone(result["target"]["sample_rate"])
        self.assertIsNone(result["scope"]["sample_rate"])
        self.assertIsNone(result["before_window"]["weight"])
        self.assertIsNone(result["after_window"]["weight"])
        self.assertIsNone(result["expected_window"]["weight"])
        self.assertIsNone(result["collateral_checks"][0]["weight"])
        self.assertIsNone(result["displacement_checks"][0]["weight"])
        json.dumps(result, allow_nan=False)

    def test_compare_posture_main_sanitizes_control_scalar_metadata(self) -> None:
        payload = json.dumps(
            {
                "comparison_type": "post_change_vs_expected",
                "change_time": float("nan"),
                "table_used": float("inf"),
                "after": {"siem_blocked_requests": 130},
                "expected": {"siem_blocked_requests": 100},
                "target_metrics": ["siem_blocked_requests"],
            }
        )
        with mock.patch("sys.argv", ["compare_posture.py", payload]):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                exit_code = self.compare_posture.main()

        document = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertIsNone(document["change_time"])
        self.assertIsNone(document["table_used"])
        self.assertEqual(stderr.getvalue(), "")

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

    def test_attribution_scaffold_normalizes_combined_rows(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "cnt_all",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_cnt_all": 180,
                        "baseline_cnt_all": 100,
                    },
                    {
                        "client_asn": "64501",
                        "current_cnt_all": 120,
                        "baseline_cnt_all": 100,
                    },
                ],
            }
        )

        self.assertEqual(result["schema_version"], "bot_attribution_report.v1")
        self.assertEqual(result["metric"], "requests")
        self.assertEqual(result["metric_kind"], "additive_count")
        self.assertEqual(result["dimensions"], ["client_asn"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertEqual(result["movers"][0]["values"]["client_asn"], "64500")
        self.assertEqual(result["movers"][0]["absolute_delta"], 80)
        self.assertEqual(result["movers"][0]["presence_lifecycle"], "existing")
        self.assertEqual(result["movers"][0]["support_change_label"], "support_increase")
        self.assertEqual(result["total_current"], 300)
        self.assertIn("trusted_context_missing", result["confidence_reasons"])

    def test_attribution_normalizes_combined_rows_to_canonical_rows(self) -> None:
        result = self.attribution.normalize_input_rows(
            {
                "metric": "cnt_all",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_cnt_all": 180,
                        "baseline_cnt_all": 100,
                    }
                ],
                "metric_kind": "ratio",
            }
        )

        self.assertEqual(result["metric"], "requests")
        self.assertEqual(result["metric_kind"], "additive_count")
        self.assertEqual(result["row_shape"], "combined")
        self.assertEqual(result["canonical_rows"][0]["dimensions"], {"client_asn": "64500"})
        self.assertEqual(result["canonical_rows"][0]["current"], 180)
        self.assertEqual(result["canonical_rows"][0]["baseline"], 100)
        self.assertEqual(
            result["input_assertions"]["caller_metric_kind_assertion"],
            "ratio",
        )

    def test_attribution_normalizes_period_split_rows_to_canonical_rows(self) -> None:
        result = self.attribution.normalize_input_rows(
            {
                "metric": "requests",
                "dimensions": ["client_asn"],
                "rows": [
                    {"period": "after", "client_asn": "64500", "requests": 180},
                    {"period": "before", "client_asn": "64500", "requests": 100},
                ],
            }
        )

        self.assertEqual(result["row_shape"], "period_split")
        self.assertEqual(result["canonical_rows"][0]["current"], 180)
        self.assertEqual(result["canonical_rows"][0]["baseline"], 100)

    def test_attribution_normalized_rows_include_baseline_value_semantic(self) -> None:
        result = self.attribution.normalize_input_rows(
            {
                "metric": "requests",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 180,
                        "baseline_requests": 100,
                    }
                ],
            }
        )

        self.assertEqual(result["baseline_value_semantic"], "raw_total_window")

    def test_attribution_normalizes_wrapper_mcp_rows(self) -> None:
        result = self.attribution.normalize_input_rows(
            {
                "metadata": {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "baseline_method": "single_previous_window",
                    "baseline_value_semantic": "raw_total_window",
                },
                "mcp_result": {
                    "columns": [
                        {"name": "client_asn"},
                        {"name": "current_requests"},
                        {"name": "baseline_requests"},
                    ],
                    "rows": [["64500", 180, 100]],
                },
            }
        )

        self.assertEqual(result["row_shape"], "combined")
        self.assertEqual(result["canonical_rows"][0]["dimensions"]["client_asn"], "64500")
        self.assertEqual(result["baseline_method"], "single_previous_window")
        self.assertEqual(result["baseline_value_semantic"], "raw_total_window")

    def test_attribution_parser_exposes_required_options(self) -> None:
        args = self.attribution.parse_args(
            [
                "--metric",
                "requests",
                "--dimensions",
                "client_asn,bot_class",
                "--min-count",
                "75",
                "--limit",
                "5",
                "--output",
                "report",
                "{}",
            ]
        )

        self.assertEqual(args.metric, "requests")
        self.assertEqual(args.dimensions, "client_asn,bot_class")
        self.assertEqual(args.min_count, 75.0)
        self.assertEqual(args.limit, 5)
        self.assertEqual(args.output, "report")

    def test_attribution_preserves_zero_min_count_option(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "bot_share_pct",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_bot_share_pct": 1,
                        "baseline_bot_share_pct": 0.5,
                        "current_support_raw": 1,
                        "baseline_support_raw": 1,
                    }
                ],
            },
            options={"min_count": 0},
        )

        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["movers"][0]["confidence"], "medium")

    def test_attribution_totals_follow_returned_rows_after_limit(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "requests",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 180,
                        "baseline_requests": 100,
                    },
                    {
                        "client_asn": "64501",
                        "current_requests": 120,
                        "baseline_requests": 100,
                    },
                ],
            },
            options={"limit": 1},
        )

        self.assertEqual(result["returned_rows"], 1)
        self.assertTrue(result["output_limit_applied"])
        self.assertEqual(result["movers"][0]["rank"], 1)
        self.assertEqual(result["total_current"], 180)
        self.assertEqual(result["total_baseline"], 100)
        self.assertEqual(result["total_delta"], 80)
        self.assertEqual(result["total_abs_delta"], 80)
        self.assertEqual(result["buckets"]["basis"], "returned_rows")

    def test_attribution_ranking_is_deterministic_with_nulls_last(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "requests",
                "dimensions": ["bot_class", "client_asn"],
                "rows": [
                    {
                        "bot_class": "search",
                        "client_asn": "64510",
                        "current_requests": 220,
                        "baseline_requests": 200,
                    },
                    {
                        "bot_class": "ai",
                        "client_asn": "64520",
                        "current_requests": 120,
                        "baseline_requests": 100,
                    },
                    {
                        "bot_class": None,
                        "client_asn": "64500",
                        "current_requests": 320,
                        "baseline_requests": 300,
                    },
                ],
            }
        )

        self.assertEqual(
            [mover["values"] for mover in result["movers"]],
            [
                {"bot_class": "ai", "client_asn": "64520"},
                {"bot_class": "search", "client_asn": "64510"},
                {"bot_class": None, "client_asn": "64500"},
            ],
        )
        self.assertEqual([mover["rank"] for mover in result["movers"]], [1, 2, 3])

    def test_attribution_excludes_unsafe_one_sided_rows_from_ranked_output(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "requests",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 220,
                        "baseline_requests": 0,
                    },
                    {
                        "client_asn": "64501",
                        "current_requests": 180,
                        "baseline_requests": 100,
                    },
                ],
            }
        )

        self.assertEqual(result["returned_rows"], 1)
        self.assertEqual(result["movers"][0]["values"]["client_asn"], "64501")
        self.assertEqual(result["total_current"], 180)
        self.assertEqual(result["buckets"]["existing_count"], 1)
        self.assertEqual(
            result["not_evaluated_components"][1]["reason"],
            "period_absence_not_trusted",
        )
        self.assertEqual(result["not_evaluated_components"][1]["skipped_count"], 1)

    def test_attribution_withholds_contribution_for_assertion_only_metadata(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "requests",
                "dimensions": ["client_asn"],
                "rowset_complete": True,
                "contribution_basis": "complete_scope_pre_limit",
                "complete_scope_total_abs_delta": 500,
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 180,
                        "baseline_requests": 100,
                        "contribution_pct": 16,
                    }
                ],
            }
        )

        self.assertFalse(result["rowset_complete"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertIn("caller_assertion_not_trusted", result["confidence_reasons"])
        self.assertEqual(result["input_assertions"]["rowset_complete"], True)
        self.assertEqual(
            result["not_evaluated_components"][0]["reason"],
            "complete_scope_not_proven",
        )
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_non_volume_metric_without_support_is_not_evaluated(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "bot_share_pct",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_bot_share_pct": 55,
                        "baseline_bot_share_pct": 40,
                    }
                ],
            }
        )

        self.assertEqual(result["confidence"], "low")
        self.assertEqual(result["movers"][0]["presence_lifecycle"], "not_evaluated")
        self.assertEqual(result["movers"][0]["support_change_label"], "not_evaluated")
        self.assertIn("lifecycle_support_missing", result["movers"][0]["confidence_reasons"])
        self.assertEqual(
            result["not_evaluated_components"][1]["reason"],
            "lifecycle_support_missing",
        )

    def test_attribution_non_volume_metric_with_explicit_support_uses_lifecycle_labels(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "bot_share_pct",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_bot_share_pct": 55,
                        "baseline_bot_share_pct": 40,
                        "current_support_raw": 180,
                        "baseline_support_raw": 120,
                    }
                ],
            }
        )

        mover = result["movers"][0]
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(mover["confidence"], "medium")
        self.assertEqual(mover["presence_lifecycle"], "existing")
        self.assertEqual(mover["support_change_label"], "support_increase")
        self.assertNotIn("lifecycle_support_missing", mover["confidence_reasons"])

    def test_attribution_additive_metric_prefers_explicit_support_for_lifecycle(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "requests",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 180,
                        "baseline_requests": 100,
                        "current_support_raw": 0,
                        "baseline_support_raw": 0,
                    },
                    {
                        "client_asn": "64501",
                        "current_requests": 120,
                        "baseline_requests": 100,
                    },
                ],
            }
        )

        self.assertEqual(result["returned_rows"], 1)
        self.assertEqual(result["movers"][0]["values"]["client_asn"], "64501")
        self.assertEqual(result["movers"][0]["presence_lifecycle"], "existing")
        self.assertEqual(
            result["not_evaluated_components"][1]["reason"],
            "period_absence_not_trusted",
        )
        self.assertEqual(result["not_evaluated_components"][1]["skipped_count"], 1)

    def test_attribution_emits_sparse_candidate_for_low_support_one_sided_row(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "requests",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 20,
                        "baseline_requests": 0,
                    }
                ],
            }
        )

        self.assertEqual(result["returned_rows"], 1)
        self.assertEqual(result["movers"][0]["presence_lifecycle"], "not_evaluated")
        self.assertEqual(result["movers"][0]["support_change_label"], "not_evaluated")
        self.assertEqual(result["movers"][0]["candidate_flags"], ["sparse_new_candidate"])
        self.assertEqual(result["buckets"]["not_evaluated_count"], 1)

    def test_attribution_zero_baseline_guard_uses_metric_math_not_lifecycle_absence(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "bot_share_pct",
                "dimensions": ["client_asn"],
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_bot_share_pct": 50,
                        "baseline_bot_share_pct": 0,
                        "current_support_raw": 180,
                        "baseline_support_raw": 120,
                    }
                ],
            }
        )

        mover = result["movers"][0]
        self.assertEqual(mover["presence_lifecycle"], "existing")
        self.assertTrue(mover["pct_change_guarded"])
        self.assertIn("zero_baseline_guard", mover["confidence_reasons"])
        self.assertEqual(result["confidence"], "medium")

    def test_attribution_never_emits_scorecard_export_output(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "requests",
                "dimensions": ["client_asn"],
                "scorecard_export_safe": True,
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 180,
                        "baseline_requests": 100,
                    }
                ],
            }
        )

        self.assertNotIn("scorecard_export_safe", result)
        self.assertNotEqual(result.get("schema_version"), "bot_scorecard_input.v1")

    def test_attribution_scorecard_safe_assertions_do_not_raise_report_confidence(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "schema_version": "bot_scorecard_input.v1",
                "metric": "requests",
                "dimensions": ["client_asn"],
                "summary_table_used": True,
                "rowset_complete": True,
                "contribution_basis": "complete_scope_pre_limit",
                "complete_scope_total_abs_delta": 500,
                "scorecard_export_safe": True,
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 180,
                        "baseline_requests": 100,
                    }
                ],
            }
        )

        mover = result["movers"][0]
        self.assertEqual(result["schema_version"], "bot_attribution_report.v1")
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(mover["confidence"], "medium")
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("scorecard_export_safe", result)
        self.assertNotIn("contribution_pct", mover)
        self.assertEqual(
            result["input_assertions"],
            {
                "rowset_complete": True,
                "contribution_basis": "complete_scope_pre_limit",
                "complete_scope_total_abs_delta": 500,
                "scorecard_export_safe": True,
            },
        )
        self.assertIn("caller_assertion_not_trusted", result["confidence_reasons"])
        self.assertIn("standalone_confidence_cap", result["confidence_reasons"])
        self.assertNotEqual(result["confidence"], "high")
        self.assertNotEqual(mover["confidence"], "high")

    def test_attribution_summary_support_validates_single_dimension_tables(self) -> None:
        host_only = self.attribution.validate_summary_table_support(
            "bot_agg_hour",
            ["request_host"],
        )
        bot_class = self.attribution.validate_summary_table_support(
            "bot_agg_ua_hour",
            ["bot_class"],
        )
        resource = self.attribution.validate_summary_table_support(
            "bot_agg_resource_day",
            ["resource_category"],
        )

        self.assertTrue(host_only["supported"])
        self.assertTrue(bot_class["supported"])
        self.assertTrue(resource["supported"])
        self.assertEqual(host_only["limitations"], [])
        self.assertEqual(host_only["retained_dimensions"], ["request_host"])

    def test_attribution_summary_support_validates_composite_dimension_sets(self) -> None:
        path_class = self.attribution.validate_summary_table_support(
            "bot_agg_path_hour",
            ["request_path_norm", "bot_class"],
            scope={"request_host": "www.example.com"},
        )
        asn = self.attribution.validate_summary_table_support(
            "bot_agg_asn_hour",
            ["request_host", "client_asn"],
        )

        self.assertTrue(path_class["supported"])
        self.assertTrue(asn["supported"])
        self.assertEqual(path_class["scope_filter_columns"], ["request_host"])
        self.assertEqual(path_class["limitations"], [])

    def test_attribution_accepts_supported_siem_dimension_and_filter(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "blocked_requests",
                "grouped_dimensions": ["client_asn"],
                "table_used": "bot_siem_filter_summary_hour",
                "summary_table_used": True,
                "scope": {"request_host": "www.example.com"},
                "filters": {"resource_category": "login"},
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_blocked_requests": 180,
                        "baseline_blocked_requests": 100,
                    }
                ],
            }
        )

        limitation_codes = {item["code"] for item in result["limitations"]}
        self.assertEqual(result["confidence"], "medium")
        self.assertTrue(result["summary_validation"]["supported"])
        self.assertIn("summary_dimension_set_supported", result["confidence_reasons"])
        self.assertNotIn("unsupported_summary_dimension_set", limitation_codes)
        self.assertNotIn("unsupported_summary_filter", limitation_codes)

    def test_attribution_rejects_request_path_and_client_asn_summary_set(self) -> None:
        validation = self.attribution.validate_summary_table_support(
            "bot_agg_path_hour",
            ["request_path_norm", "client_asn"],
        )

        self.assertFalse(validation["supported"])
        self.assertEqual(validation["unsupported_grouped_dimensions"], ["client_asn"])
        self.assertIn("unsupported_summary_dimension_set", validation["limitations"])

    def test_attribution_emits_unsupported_summary_filter_limitation(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "metric": "requests",
                "dimensions": ["request_host"],
                "table_used": "bot_agg_hour",
                "summary_table_used": True,
                "filters": {"client_asn": "64500"},
                "rows": [
                    {
                        "request_host": "www.example.com",
                        "current_requests": 180,
                        "baseline_requests": 100,
                    }
                ],
            }
        )

        limitation_codes = {item["code"] for item in result["limitations"]}
        unsupported_components = [
            component
            for component in result["not_evaluated_components"]
            if component["reason"] == "unsupported_summary_filter"
        ]
        self.assertEqual(result["confidence"], "low")
        self.assertIn("unsupported_summary_filter", limitation_codes)
        self.assertEqual(unsupported_components[0]["unsupported_columns"], ["client_asn"])
        self.assertEqual(unsupported_components[0]["selected_table"], "bot_agg_hour")

    def test_attribution_rejects_unsupported_siem_dimension_and_filter(self) -> None:
        validation = self.attribution.validate_summary_table_support(
            "bot_siem_summary_hour",
            ["akamai_canonical_bot_class"],
            filters={"resource_category": "api"},
        )

        self.assertFalse(validation["supported"])
        self.assertEqual(
            validation["unsupported_grouped_dimensions"],
            ["akamai_canonical_bot_class"],
        )
        self.assertEqual(validation["unsupported_filter_columns"], ["resource_category"])
        self.assertIn("unsupported_summary_dimension_set", validation["limitations"])
        self.assertIn("unsupported_summary_filter", validation["limitations"])

    def test_attribution_raw_fallback_and_fixture_metadata_stay_below_high(self) -> None:
        raw_result = self.attribution.normalize_attribution(
            {
                "metric": "requests",
                "dimensions": ["request_path_norm", "client_asn"],
                "table_used": "bot_detection",
                "summary_table_used": False,
                "generator_name": "bot-insights-attribution-sql",
                "metadata_fixture_identity": "fixture:bot_detection:v1",
                "rows": [
                    {
                        "request_path_norm": "/login",
                        "client_asn": "64500",
                        "current_requests": 180,
                        "baseline_requests": 100,
                    }
                ],
            }
        )
        fixture_result = self.attribution.normalize_attribution(
            {
                "metric": "requests",
                "dimensions": ["client_asn"],
                "table_used": "bot_summary_hour",
                "summary_table_used": True,
                "generator_name": "bot-insights-attribution-sql",
                "metadata_fixture_identity": "fixture:bot_summary_hour:v1",
                "metadata_fingerprint": "sha256:fixture",
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 180,
                        "baseline_requests": 100,
                    }
                ],
            },
            trusted_context={"trusted_evidence": []},
        )

        self.assertEqual(raw_result["confidence"], "low")
        self.assertIn("raw_table_fallback", raw_result["confidence_reasons"])
        self.assertEqual(fixture_result["confidence"], "medium")
        self.assertIn(
            "trusted_context_reserved_for_future_tasks",
            fixture_result["confidence_reasons"],
        )
        self.assertNotEqual(raw_result["confidence"], "high")
        self.assertNotEqual(fixture_result["confidence"], "high")

    def test_attribution_result_digest_is_deterministic_and_rejects_nonfinite_values(self) -> None:
        first = self.trusted_attribution_input(
            rows=[
                {
                    "client_asn": "64501",
                    "current_requests": 120,
                    "baseline_requests": 100,
                    "current_support_raw": 120,
                    "baseline_support_raw": 100,
                },
                {
                    "client_asn": "64500",
                    "current_requests": 180,
                    "baseline_requests": 100,
                    "current_support_raw": 180,
                    "baseline_support_raw": 100,
                },
            ]
        )
        second = self.trusted_attribution_input(rows=list(reversed(first["rows"])))
        self.assertEqual(
            self.attribution.result_digest_v1(first),
            self.attribution.result_digest_v1(second),
        )

        invalid = self.trusted_attribution_input(
            rows=[
                {
                    "client_asn": "64500",
                    "current_requests": float("nan"),
                    "baseline_requests": 100,
                }
            ]
        )
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.result_digest_v1(invalid)
        self.assert_invalid_input_code(exc.exception, "non_finite_digest_value")

    def test_attribution_result_digest_binds_contribution_fields(self) -> None:
        first = self.trusted_attribution_input()
        second = self.trusted_attribution_input()
        second["rows"][0]["contribution_pct"] = 50

        self.assertNotEqual(
            self.attribution.result_digest_v1(first),
            self.attribution.result_digest_v1(second),
        )

        third = self.trusted_attribution_input()
        fourth = self.trusted_attribution_input()
        third["complete_scope_total_abs_delta"] = 80
        fourth["complete_scope_total_abs_delta"] = 81
        self.assertNotEqual(
            self.attribution.result_digest_v1(third),
            self.attribution.result_digest_v1(fourth),
        )

        invalid = self.trusted_attribution_input()
        invalid["rows"][0]["complete_scope_total_abs_delta"] = float("inf")
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.result_digest_v1(invalid)
        self.assert_invalid_input_code(exc.exception, "non_finite_digest_value")

    def test_attribution_trusted_context_digest_mismatch_degrades(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc, result_digest="sha256:" + "9" * 64)

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotEqual(result["confidence"], "high")
        self.assertIn("trusted_context_digest_mismatch", result["confidence_reasons"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertFalse(result["trusted_context_validation"]["valid"])

    def test_attribution_missing_fingerprints_degrade_trusted_context(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        context.pop("query_fingerprint")
        context.pop("result_digest")

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertIn("query_fingerprint_missing", result["confidence_reasons"])
        self.assertIn("result_digest_missing", result["confidence_reasons"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotEqual(result["confidence"], "high")

    def test_attribution_manual_origin_trusted_context_is_invalid(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        context["result_origin"] = "manual_paste"

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertIn("trusted_context_invalid", result["confidence_reasons"])
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertEqual(result["contribution_basis"], "none")

    def test_attribution_duplicate_evidence_ids_invalidate_trusted_context(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        duplicate = dict(context["trusted_evidence"][0])
        context["trusted_evidence"].append(duplicate)

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertIn("trusted_evidence_mismatch", result["confidence_reasons"])
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertEqual(result["contribution_basis"], "none")

    def test_attribution_evidence_contract_mismatch_degrades_to_no_contribution(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        context["trusted_evidence"][0]["metric"] = "blocked_requests"

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertIn("trusted_evidence_mismatch", result["confidence_reasons"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_incomplete_evidence_contract_is_invalid(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        context["trusted_evidence"][0].pop("baseline_value_semantic")

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertIn("trusted_evidence_mismatch", result["confidence_reasons"])
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertEqual(result["contribution_basis"], "none")

    def test_attribution_invalid_evidence_degrades_to_no_contribution(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        context["trusted_evidence"][0]["baseline_normalization"] = dict(
            context["trusted_evidence"][0]["baseline_normalization"]
        )
        context["trusted_evidence"][0]["baseline_normalization"]["factor"] = float("nan")

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertIn("trusted_evidence_mismatch", result["confidence_reasons"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotEqual(result["confidence"], "high")

    def test_attribution_valid_provided_contribution_fixture_remains_disabled_without_wrapper(self) -> None:
        input_doc = self.trusted_attribution_input(
            rows=[
                {
                    "client_asn": "64500",
                    "current_requests": 180,
                    "baseline_requests": 100,
                    "current_support_raw": 180,
                    "baseline_support_raw": 100,
                    "absolute_delta": 80,
                    "complete_scope_total_abs_delta": 100,
                    "contribution_pct": 80,
                },
                {
                    "client_asn": "64501",
                    "current_requests": 120,
                    "baseline_requests": 100,
                    "current_support_raw": 120,
                    "baseline_support_raw": 100,
                    "absolute_delta": 20,
                    "complete_scope_total_abs_delta": 100,
                    "contribution_pct": 20,
                },
            ]
        )
        context = self.provided_contribution_context_for(input_doc)

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertTrue(result["trusted_context_validation"]["valid"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertEqual(result["trusted_context_validation"]["evidence_types"], ["provided_contribution_evidence"])
        self.assertIn("trusted_wrapper_unavailable", result["confidence_reasons"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_provided_contribution_evidence_requires_matching_field_identity(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.provided_contribution_context_for(input_doc)
        context["trusted_evidence"][0]["denominator_field"] = "caller_total_abs_delta"

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertIn("provided_contribution_inconsistent", result["confidence_reasons"])
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_invalid_provided_contribution_entry_invalidates_evidence_list(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        provided = dict(context["trusted_evidence"][0])
        provided.update(
            {
                "evidence_id": "provided-contribution-inconsistent-v1",
                "evidence_type": "provided_contribution_evidence",
                "reviewed_metric_kind": "additive_count",
                "contribution_pct_field": "contribution_pct",
                "denominator_field": "caller_total_abs_delta",
                "pre_denominator_filter_applied": False,
                "per_row_contribution": True,
                "contribution_pct_tolerance_pp": 0.01,
            }
        )
        context["trusted_evidence"].append(provided)

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertIn("provided_contribution_inconsistent", result["confidence_reasons"])
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertEqual(
            result["trusted_context_validation"]["evidence_types"],
            ["complete_scope_pre_limit_evidence", "provided_contribution_evidence"],
        )
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_provided_contribution_evidence_requires_row_math_consistency(self) -> None:
        input_doc = self.trusted_attribution_input(
            rows=[
                {
                    "client_asn": "64500",
                    "current_requests": 180,
                    "baseline_requests": 100,
                    "current_support_raw": 180,
                    "baseline_support_raw": 100,
                    "absolute_delta": 80,
                    "complete_scope_total_abs_delta": 100,
                    "contribution_pct": 70,
                },
                {
                    "client_asn": "64501",
                    "current_requests": 120,
                    "baseline_requests": 100,
                    "current_support_raw": 120,
                    "baseline_support_raw": 100,
                    "absolute_delta": 20,
                    "complete_scope_total_abs_delta": 100,
                    "contribution_pct": 20,
                },
            ]
        )
        context = self.provided_contribution_context_for(input_doc)

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertIn("provided_contribution_inconsistent", result["confidence_reasons"])
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_valid_future_wrapper_fixture_remains_disabled_without_wrapper(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)

        result = self.attribution.normalize_attribution(input_doc, trusted_context=context)

        self.assertTrue(result["trusted_context_validation"]["valid"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertEqual(result["trusted_context_validation"]["evidence_types"], ["complete_scope_pre_limit_evidence"])
        self.assertIn("trusted_wrapper_unavailable", result["confidence_reasons"])
        self.assertIn(
            "trusted_context_reserved_for_future_tasks",
            result["confidence_reasons"],
        )
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotEqual(result["confidence"], "high")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_caller_side_trusted_fields_are_assertions_only(self) -> None:
        input_doc = self.trusted_attribution_input()
        input_doc["trusted_evidence"] = [
            {
                "evidence_id": "caller-complete-scope",
                "evidence_type": "complete_scope_pre_limit_evidence",
            }
        ]
        input_doc["result_digest"] = self.attribution.result_digest_v1(input_doc)
        input_doc["evidence_source"] = "trusted_template_generator"

        result = self.attribution.normalize_attribution(input_doc)

        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotEqual(result["confidence"], "high")
        self.assertIn("caller_assertion_not_trusted", result["confidence_reasons"])
        self.assertIn("trusted_context_missing", result["confidence_reasons"])
        self.assertIn("trusted_evidence", result["input_assertions"])
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_duplicate_aggregation_evidence_does_not_unlock_local_aggregation(self) -> None:
        input_doc = self.trusted_attribution_input(
            rows=[
                {"period": "current", "client_asn": "64500", "requests": 100},
                {"period": "current", "client_asn": "64500", "requests": 80},
                {"period": "baseline", "client_asn": "64500", "requests": 100},
            ]
        )
        context = self.trusted_context_for(self.trusted_attribution_input())
        context["trusted_evidence"][0].update(
            {
                "evidence_id": "duplicate-aggregation-partitioned-period-rows-v1",
                "evidence_type": "duplicate_aggregation_evidence",
                "duplicate_key_fields": ["period", "client_asn"],
                "partition_fields": ["hdx_cdn"],
                "partition_semantics": "disjoint_source_partitions",
                "aggregation_allowed": True,
            }
        )

        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_attribution(input_doc, trusted_context=context)
        self.assert_invalid_input_code(exc.exception, "duplicate_entity_period_key")

    def test_attribution_metadata_fingerprint_is_stable_across_key_order(self) -> None:
        first = {
            "table": "bot_summary_hour",
            "database": "bot_insights",
            "is_summary_table": True,
            "columns": [
                {
                    "name": "cnt_all",
                    "type": "AggregateFunction(sum, UInt64)",
                    "column_category": "AggregateColumn",
                    "merge_function": "sumMerge",
                },
                {"name": "request_host", "type": "String", "column_category": "Column"},
            ],
        }
        second = {
            "columns": [
                {"column_category": "Column", "type": "String", "name": "request_host"},
                {
                    "merge_function": "sumMerge",
                    "column_category": "AggregateColumn",
                    "type": "AggregateFunction(sum, UInt64)",
                    "name": "cnt_all",
                },
            ],
            "is_summary_table": True,
            "database": "bot_insights",
            "table": "bot_summary_hour",
        }

        first_hash = self.attribution.metadata_fingerprint(
            first,
            selected_columns=["request_host", "cnt_all"],
            metadata_fixture_identity="fixture:bot_summary_hour:v1",
        )
        second_hash = self.attribution.metadata_fingerprint(
            second,
            selected_columns=["cnt_all", "request_host"],
            metadata_fixture_identity="fixture:bot_summary_hour:v1",
        )

        self.assertEqual(first_hash, second_hash)
        self.assertTrue(first_hash.startswith("sha256:"))

    def test_attribution_sql_template_rejects_non_summary_metadata(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.render_attribution_sql_template(
                table_metadata={
                    "table": "bot_summary_hour",
                    "database": "bot_insights",
                    "is_summary_table": False,
                    "columns": [
                        {"name": "timestamp", "type": "DateTime", "column_category": "Column"},
                        {"name": "request_host", "type": "String", "column_category": "Column"},
                        {"name": "client_asn", "type": "String", "column_category": "Column"},
                        {
                            "name": "sum(cnt_all)",
                            "type": "AggregateFunction(sum, UInt64)",
                            "column_category": "AggregateColumn",
                            "merge_function": "sumMerge",
                        },
                    ],
                },
                metric="requests",
                dimensions=["client_asn"],
                scope={"request_host": "www.example.com"},
                current_window={
                    "start": "2026-04-01T00:00:00Z",
                    "end": "2026-04-02T00:00:00Z",
                },
                baseline_windows=[
                    {
                        "start": "2026-03-01T00:00:00Z",
                        "end": "2026-03-02T00:00:00Z",
                    }
                ],
                baseline_method="single_previous_window",
                output_limit=25,
            )

        self.assert_invalid_input_code(exc.exception, "table_metadata_not_summary_table")

    def test_attribution_sql_template_uses_metadata_merge_expressions(self) -> None:
        table_metadata = {
            "table": "bot_summary_hour",
            "database": "bot_insights",
            "is_summary_table": True,
            "columns": [
                {"name": "timestamp", "type": "DateTime", "column_category": "Column"},
                {"name": "request_host", "type": "String", "column_category": "Column"},
                {"name": "client_asn", "type": "String", "column_category": "Column"},
                {
                    "name": "sum(cnt_all)",
                    "type": "AggregateFunction(sum, UInt64)",
                    "column_category": "AggregateColumn",
                    "merge_function": "sumMerge",
                },
            ],
        }

        result = self.attribution.render_attribution_sql_template(
            table_metadata=table_metadata,
            metric="cnt_all",
            dimensions=["client_asn"],
            scope={"request_host": "www.example.com"},
            current_window={
                "start": "2026-04-01T00:00:00Z",
                "end": "2026-04-02T00:00:00Z",
            },
            baseline_windows=[
                {
                    "start": "2026-02-01T00:00:00Z",
                    "end": "2026-02-02T00:00:00Z",
                    "label": "non_adjacent_previous_day",
                }
            ],
            baseline_method="single_previous_window",
            output_limit=25,
            metadata_fixture_identity="fixture:bot_summary_hour:v1",
        )

        sql = result["sql"]
        provenance = result["provenance"]
        evidence = result["evidence_assertions"]
        evidence_types = {item["evidence_type"] for item in evidence}

        self.assertEqual(result["schema_version"], "bot_attribution_sql_template.v1")
        self.assertIn("current_by_entity AS", sql)
        self.assertIn("baseline_window_1_by_entity AS", sql)
        self.assertIn("baseline_by_entity AS", sql)
        self.assertIn("sumMerge(`sum(cnt_all)`) AS current_requests", sql)
        self.assertIn("sumMerge(`sum(cnt_all)`) AS baseline_window_requests", sql)
        self.assertIn("`timestamp` >= current_start", sql)
        self.assertIn("`timestamp` < current_end", sql)
        self.assertIn("`timestamp` >= baseline_start_1", sql)
        self.assertIn("`timestamp` < baseline_end_1", sql)
        self.assertNotIn("`timestamp` >= baseline_start_1\n      AND `timestamp` < current_end", sql)
        self.assertIn("FULL OUTER JOIN baseline_by_entity AS b USING (`client_asn`)", sql)
        self.assertIn(
            "sum(abs(current_requests - baseline_requests)) OVER () AS complete_scope_total_abs_delta",
            sql,
        )
        self.assertIn("FROM scored", sql)
        self.assertIn("ORDER BY abs_delta DESC, toString(`client_asn`) ASC", sql)
        self.assertIn("LIMIT 25", sql)
        self.assertEqual(provenance["template_id"], "full_scope_joined_pre_limit_v1")
        self.assertEqual(provenance["metric"], "requests")
        self.assertEqual(provenance["metric_expression"], "sumMerge(`sum(cnt_all)`)")
        self.assertEqual(provenance["support_expression"], "sumMerge(`sum(cnt_all)`)")
        self.assertEqual(
            provenance["merge_expressions"],
            {"sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"},
        )
        self.assertIn("sum(cnt_all)", provenance["selected_columns"])
        self.assertTrue(provenance["metadata_fingerprint"].startswith("sha256:"))
        self.assertTrue(provenance["query_fingerprint"].startswith("sha256:"))
        self.assertEqual(provenance["limit_stage"], "after_denominator")
        self.assertEqual(
            evidence_types,
            {"complete_scope_pre_limit_evidence", "zero_fill_evidence"},
        )
        complete_evidence = [
            item for item in evidence if item["evidence_type"] == "complete_scope_pre_limit_evidence"
        ][0]
        zero_fill = [item for item in evidence if item["evidence_type"] == "zero_fill_evidence"][0]
        self.assertTrue(complete_evidence["computed_before_output_limit"])
        self.assertFalse(complete_evidence["source_limit_applied_before_denominator"])
        self.assertEqual(
            zero_fill["period_value_trust"],
            {
                "current": "trusted_full_scope_join",
                "baseline": "trusted_full_scope_join",
            },
        )

        def walk(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    yield key, nested
                    yield from walk(nested)
            elif isinstance(value, list):
                for nested in value:
                    yield from walk(nested)

        flattened = list(walk(result))
        self.assertNotIn(("result_digest", mock.ANY), flattened)
        self.assertFalse(any(value == "high" for _, value in flattened))
        self.assertFalse(any(key == "scorecard_export_safe" for key, _ in flattened))
        self.assertFalse(any(value == "bot_scorecard_input.v1" for _, value in flattened))

    def test_attribution_sql_template_applies_filters_to_all_period_ctes(self) -> None:
        table_metadata = {
            "table": "bot_summary_hour",
            "database": "bot_insights",
            "is_summary_table": True,
            "columns": [
                {"name": "timestamp", "type": "DateTime", "column_category": "Column"},
                {"name": "request_host", "type": "String", "column_category": "Column"},
                {"name": "client_asn", "type": "String", "column_category": "Column"},
                {"name": "bot_class", "type": "String", "column_category": "Column"},
                {"name": "ai_category", "type": "String", "column_category": "Column"},
                {
                    "name": "sum(cnt_all)",
                    "type": "AggregateFunction(sum, UInt64)",
                    "column_category": "AggregateColumn",
                    "merge_function": "sumMerge",
                },
            ],
        }
        kwargs = {
            "table_metadata": table_metadata,
            "metric": "requests",
            "dimensions": ["client_asn"],
            "scope": {"request_host": "www.example.com"},
            "filters": {"bot_class": "good"},
            "applied_scope_filters": {"ai_category": ["search", "training"]},
            "current_window": {
                "start": "2026-04-01T00:00:00Z",
                "end": "2026-04-02T00:00:00Z",
            },
            "baseline_windows": [
                {
                    "start": "2026-03-01T00:00:00Z",
                    "end": "2026-03-02T00:00:00Z",
                }
            ],
            "baseline_method": "single_previous_window",
            "output_limit": 25,
        }

        result = self.attribution.render_attribution_sql_template(**kwargs)
        changed_filter = self.attribution.render_attribution_sql_template(
            **{**kwargs, "filters": {"bot_class": "bad"}}
        )
        sql = result["sql"]
        provenance = result["provenance"]

        self.assertEqual(sql.count("`request_host` = 'www.example.com'"), 2)
        self.assertEqual(sql.count("`bot_class` = 'good'"), 2)
        self.assertEqual(sql.count("`ai_category` IN ('search', 'training')"), 2)
        self.assertIn("bot_class", provenance["selected_columns"])
        self.assertIn("ai_category", provenance["selected_columns"])
        self.assertEqual(provenance["filters"], {"bot_class": "good"})
        self.assertEqual(provenance["applied_scope_filters"], {"ai_category": ["search", "training"]})
        self.assertEqual(
            provenance["sql_predicates"],
            {
                "ai_category": ["search", "training"],
                "bot_class": "good",
                "request_host": "www.example.com",
            },
        )
        self.assertNotEqual(
            provenance["query_fingerprint"],
            changed_filter["provenance"]["query_fingerprint"],
        )

    def test_attribution_sql_template_rejects_conflicting_filter_predicates(self) -> None:
        table_metadata = {
            "table": "bot_summary_hour",
            "database": "bot_insights",
            "is_summary_table": True,
            "columns": [
                {"name": "timestamp", "type": "DateTime", "column_category": "Column"},
                {"name": "request_host", "type": "String", "column_category": "Column"},
                {"name": "client_asn", "type": "String", "column_category": "Column"},
                {"name": "bot_class", "type": "String", "column_category": "Column"},
                {
                    "name": "sum(cnt_all)",
                    "type": "AggregateFunction(sum, UInt64)",
                    "column_category": "AggregateColumn",
                    "merge_function": "sumMerge",
                },
            ],
        }

        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.render_attribution_sql_template(
                table_metadata=table_metadata,
                metric="requests",
                dimensions=["client_asn"],
                scope={"request_host": "www.example.com", "bot_class": "good"},
                filters={"bot_class": "bad"},
                current_window={
                    "start": "2026-04-01T00:00:00Z",
                    "end": "2026-04-02T00:00:00Z",
                },
                baseline_windows=[
                    {
                        "start": "2026-03-01T00:00:00Z",
                        "end": "2026-03-02T00:00:00Z",
                    }
                ],
                baseline_method="single_previous_window",
                output_limit=25,
            )

        self.assert_invalid_input_code(exc.exception, "scope_filter_conflict")

    def test_attribution_sql_template_multiple_baselines_are_explicit_and_deterministic(self) -> None:
        table_metadata = {
            "table": "bot_agg_path_day",
            "database": "bot_insights",
            "is_summary_table": True,
            "columns": [
                {"name": "timestamp", "type": "DateTime", "column_category": "Column"},
                {"name": "request_host", "type": "String", "column_category": "Column"},
                {"name": "request_path_norm", "type": "String", "column_category": "Column"},
                {"name": "bot_class", "type": "String", "column_category": "Column"},
                {
                    "name": "sum(cnt_all)",
                    "type": "AggregateFunction(sum, UInt64)",
                    "column_category": "AggregateColumn",
                    "merge_function": "sumMerge",
                },
            ],
        }
        kwargs = {
            "table_metadata": table_metadata,
            "metric": "requests",
            "dimensions": ["request_path_norm", "bot_class"],
            "scope": {"request_host": "www.example.com"},
            "current_window": {
                "start": "2026-04-01T00:00:00Z",
                "end": "2026-04-08T00:00:00Z",
            },
            "baseline_windows": [
                {
                    "start": "2026-02-01T00:00:00Z",
                    "end": "2026-02-08T00:00:00Z",
                    "label": "baseline_week_1",
                },
                {
                    "start": "2026-03-01T00:00:00Z",
                    "end": "2026-03-08T00:00:00Z",
                    "label": "baseline_week_2",
                },
            ],
            "baseline_method": "duration_weighted_mean_of_baseline_windows",
            "output_limit": 10,
            "metadata_fixture_identity": "fixture:bot_agg_path_day:v1",
        }

        first = self.attribution.render_attribution_sql_template(**kwargs)
        second = self.attribution.render_attribution_sql_template(**kwargs)
        sql = first["sql"]

        self.assertEqual(
            first["provenance"]["query_fingerprint"],
            second["provenance"]["query_fingerprint"],
        )
        self.assertIn("baseline_window_1_by_entity AS", sql)
        self.assertIn("baseline_window_2_by_entity AS", sql)
        self.assertIn("`timestamp` >= baseline_start_1", sql)
        self.assertIn("`timestamp` < baseline_end_1", sql)
        self.assertIn("`timestamp` >= baseline_start_2", sql)
        self.assertIn("`timestamp` < baseline_end_2", sql)
        self.assertNotIn("baseline_start_1 AND `timestamp` < current_end", sql)
        self.assertIn("UNION ALL", sql)
        self.assertIn("GROUP BY `request_path_norm`, `bot_class`", sql)
        self.assertIn("sum(baseline_window_requests) AS baseline_raw_requests", sql)
        self.assertIn(
            "toFloat64(current_duration_seconds) / nullIf(baseline_total_duration_seconds, 0) AS baseline_normalization_factor",
            sql,
        )
        self.assertNotIn(
            "sum(baseline_window_requests * baseline_window_duration_seconds) / nullIf(sum(baseline_window_duration_seconds), 0) AS baseline_raw_requests",
            sql,
        )
        self.assertIn(
            "ORDER BY abs_delta DESC, toString(`request_path_norm`) ASC, toString(`bot_class`) ASC",
            sql,
        )
        self.assertEqual(len(first["provenance"]["baseline_windows"]), 2)
        self.assertEqual(first["provenance"]["limit_stage"], "after_denominator")

    def test_attribution_sql_template_mean_baseline_normalizes_average_window_duration(self) -> None:
        table_metadata = {
            "table": "bot_agg_path_day",
            "database": "bot_insights",
            "is_summary_table": True,
            "columns": [
                {"name": "timestamp", "type": "DateTime", "column_category": "Column"},
                {"name": "request_host", "type": "String", "column_category": "Column"},
                {"name": "request_path_norm", "type": "String", "column_category": "Column"},
                {"name": "bot_class", "type": "String", "column_category": "Column"},
                {
                    "name": "sum(cnt_all)",
                    "type": "AggregateFunction(sum, UInt64)",
                    "column_category": "AggregateColumn",
                    "merge_function": "sumMerge",
                },
            ],
        }

        result = self.attribution.render_attribution_sql_template(
            table_metadata=table_metadata,
            metric="requests",
            dimensions=["request_path_norm", "bot_class"],
            scope={"request_host": "www.example.com"},
            current_window={
                "start": "2026-04-01T00:00:00Z",
                "end": "2026-04-08T00:00:00Z",
            },
            baseline_windows=[
                {
                    "start": "2026-02-01T00:00:00Z",
                    "end": "2026-02-08T00:00:00Z",
                    "label": "baseline_week_1",
                },
                {
                    "start": "2026-03-01T00:00:00Z",
                    "end": "2026-03-08T00:00:00Z",
                    "label": "baseline_week_2",
                },
            ],
            baseline_method="mean_of_baseline_windows",
            output_limit=10,
        )

        sql = result["sql"]
        self.assertIn("avg(baseline_window_requests) AS baseline_raw_requests", sql)
        self.assertIn(
            "toFloat64(baseline_total_duration_seconds) / nullIf(baseline_window_count, 0) AS baseline_average_duration_seconds",
            sql,
        )
        self.assertIn(
            "toFloat64(current_duration_seconds) / nullIf(baseline_average_duration_seconds, 0) AS baseline_normalization_factor",
            sql,
        )
        self.assertEqual(
            result["provenance"]["baseline_normalization"]["factor_expression"],
            "current_duration_seconds / baseline_average_duration_seconds",
        )

    def test_attribution_rejects_mixed_row_shapes(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "rows": [
                        {
                            "client_asn": "64500",
                            "current_requests": 180,
                            "baseline_requests": 100,
                        },
                        {
                            "period": "current",
                            "client_asn": "64501",
                            "requests": 120,
                        },
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "mixed_row_shapes")

    def test_attribution_rejects_missing_requested_dimension(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn", "bot_class"],
                    "rows": [
                        {
                            "client_asn": "64500",
                            "current_requests": 180,
                            "baseline_requests": 100,
                        }
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "missing_requested_dimension")

    def test_attribution_rejects_non_scalar_requested_dimension_value(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "rows": [
                        {
                            "client_asn": ["64500"],
                            "current_requests": 180,
                            "baseline_requests": 100,
                        }
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "non_scalar_dimension_value")

    def test_attribution_rejects_ambiguous_metric_input(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "dimensions": ["client_asn"],
                    "rows": [
                        {
                            "client_asn": "64500",
                            "current_requests": 180,
                            "baseline_requests": 100,
                            "current_blocked_requests": 25,
                            "baseline_blocked_requests": 10,
                        }
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "ambiguous_metric_input")

    def test_attribution_rejects_duplicate_mcp_columns(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "columns": ["client_asn", "client_asn", "current_requests"],
                    "rows": [["64500", "64500", 180]],
                }
            )

        self.assert_invalid_input_code(exc.exception, "duplicate_mcp_column")

    def test_attribution_rejects_blank_mcp_columns(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "columns": ["client_asn", " ", "baseline_requests"],
                    "rows": [["64500", 180, 100]],
                }
            )

        self.assert_invalid_input_code(exc.exception, "blank_mcp_column")

    def test_attribution_rejects_non_string_mcp_columns(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "columns": [None, "current_requests", "baseline_requests"],
                    "rows": [["64500", 180, 100]],
                }
            )

        self.assert_invalid_input_code(exc.exception, "invalid_mcp_column")

    def test_attribution_rejects_non_string_named_mcp_columns(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "columns": [{"name": 7}, "current_requests", "baseline_requests"],
                    "rows": [["64500", 180, 100]],
                }
            )

        self.assert_invalid_input_code(exc.exception, "invalid_mcp_column")

    def test_attribution_rejects_mcp_row_length_mismatch(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "columns": ["client_asn", "current_requests", "baseline_requests"],
                    "rows": [["64500", 180]],
                }
            )

        self.assert_invalid_input_code(exc.exception, "mcp_row_length_mismatch")

    def test_attribution_rejects_duplicate_combined_entity_keys(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "rows": [
                        {
                            "client_asn": "64500",
                            "current_requests": 180,
                            "baseline_requests": 100,
                        },
                        {
                            "client_asn": "64500",
                            "current_requests": 120,
                            "baseline_requests": 110,
                        },
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "duplicate_entity_key")

    def test_attribution_rejects_duplicate_period_split_entity_period_keys(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "rows": [
                        {"period": "current", "client_asn": "64500", "requests": 180},
                        {"period": "current", "client_asn": "64500", "requests": 120},
                        {"period": "baseline", "client_asn": "64500", "requests": 100},
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "duplicate_entity_period_key")

    def test_attribution_rejects_period_split_rows_with_combined_metric_aliases(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "rows": [
                        {"period": "current", "client_asn": "64500", "current_requests": 180},
                        {"period": "baseline", "client_asn": "64500", "baseline_requests": 100},
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "no_usable_metric_values")

    def test_attribution_rejects_labeled_multi_baseline_rows_even_without_duplicates(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "baseline_method": "mean_of_baseline_windows",
                    "baseline_windows": [
                        {"label": "week_1"},
                        {"label": "week_2"},
                    ],
                    "rows": [
                        {"period": "current", "client_asn": "64500", "requests": 180},
                        {
                            "period": "baseline",
                            "client_asn": "64500",
                            "requests": 100,
                            "baseline_window_label": "week_1",
                        },
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "baseline_windows_not_reduced")

    def test_attribution_rejects_unreduced_multi_baseline_rows(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "baseline_method": "mean_of_baseline_windows",
                    "baseline_windows": [
                        {"label": "week_1"},
                        {"label": "week_2"},
                    ],
                    "rows": [
                        {"period": "current", "client_asn": "64500", "requests": 180},
                        {
                            "period": "baseline",
                            "client_asn": "64500",
                            "requests": 90,
                            "baseline_window_label": "week_1",
                        },
                        {
                            "period": "baseline",
                            "client_asn": "64500",
                            "requests": 110,
                            "baseline_window_label": "week_2",
                        },
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "baseline_windows_not_reduced")

    def test_attribution_dimension_inference_skips_non_scalar_columns(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "rows": [
                        {
                            "entity": {"client_asn": "64500"},
                            "current_requests": 180,
                            "baseline_requests": 100,
                        }
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "no_inferable_dimensions")

    def test_attribution_main_file_input_passes_trusted_context_none(self) -> None:
        payload = {
            "rows": [
                {
                    "client_asn": "64500",
                    "current_requests": 180,
                    "baseline_requests": 100,
                }
            ]
        }
        calls: list[dict[str, object]] = []

        def fake_normalize(input_doc, trusted_context=None, *, options=None):
            calls.append(
                {
                    "input_doc": input_doc,
                    "trusted_context": trusted_context,
                    "options": options,
                }
            )
            return {"schema_version": "bot_attribution_report.v1", "movers": []}

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            with mock.patch.object(
                self.attribution,
                "normalize_attribution",
                side_effect=fake_normalize,
            ):
                with mock.patch("sys.stdout", new=io.StringIO()):
                    exit_code = self.attribution.main(
                        [
                            "--file",
                            handle.name,
                            "--metric",
                            "requests",
                            "--dimensions",
                            "client_asn",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0]["trusted_context"], None)
        self.assertEqual(calls[0]["options"]["metric"], "requests")

    def test_attribution_main_stdin_input_passes_trusted_context_none(self) -> None:
        payload = json.dumps(
            {
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 180,
                        "baseline_requests": 100,
                    }
                ]
            }
        )
        calls: list[dict[str, object]] = []

        def fake_normalize(input_doc, trusted_context=None, *, options=None):
            calls.append(
                {
                    "input_doc": input_doc,
                    "trusted_context": trusted_context,
                    "options": options,
                }
            )
            return {"schema_version": "bot_attribution_report.v1", "movers": []}

        with mock.patch.object(
            self.attribution,
            "normalize_attribution",
            side_effect=fake_normalize,
        ):
            with mock.patch("sys.stdin", new=io.StringIO(payload)):
                with mock.patch("sys.stdout", new=io.StringIO()):
                    exit_code = self.attribution.main(
                        [
                            "--metric",
                            "requests",
                            "--dimensions",
                            "client_asn",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0]["trusted_context"], None)
        self.assertEqual(calls[0]["options"]["dimensions"], "client_asn")

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

    def test_scorecard_rejects_direct_advanced_attribution_report(self) -> None:
        with self.assertRaises(self.scorecard.InvalidScorecardInputError) as exc:
            self.scorecard.build_artifacts(
                {
                    "schema_version": "bot_attribution_report.v1",
                    "dimensions": ["client_asn"],
                    "movers": [
                        {
                            "values": {"client_asn": "64500"},
                            "current": 1500,
                            "baseline": 500,
                        }
                    ],
                }
            )

        self.assertEqual(
            exc.exception.document["errors"][0]["code"],
            "advanced_attribution_report_not_scorecard_input",
        )

    def test_scorecard_rejects_self_attesting_scorecard_input_without_context(self) -> None:
        with self.assertRaises(self.scorecard.InvalidScorecardInputError) as exc:
            self.scorecard.build_artifacts(
                {
                    "schema_version": "bot_scorecard_input.v1",
                    "scorecard_export_safe": True,
                    "entity_type": "client_asn",
                    "rows": [
                        {
                            "client_asn": "64500",
                            "current_requests": 1500,
                            "baseline_requests": 500,
                            "contribution_pct": 80,
                        }
                    ],
                }
            )

        self.assertEqual(
            exc.exception.document["errors"][0]["code"],
            "scorecard_trusted_context_missing",
        )

    def test_scorecard_rejects_non_finite_error_details_as_json_safe(self) -> None:
        with self.assertRaises(self.scorecard.InvalidScorecardInputError) as exc:
            self.scorecard.build_artifacts(
                {
                    "schema_version": "bot_scorecard_input.v1",
                    "scorecard_export_safe": float("nan"),
                    "entity_type": "client_asn",
                    "rows": [],
                }
            )

        error = exc.exception.document["errors"][0]
        self.assertEqual(error["code"], "scorecard_trusted_context_missing")
        self.assertIsNone(error["details"]["scorecard_export_safe"])
        json.dumps(exc.exception.document, allow_nan=False)

    def test_scorecard_main_rejects_non_finite_error_details_without_traceback(
        self,
    ) -> None:
        payload = json.dumps(
            {
                "schema_version": "bot_scorecard_input.v1",
                "scorecard_export_safe": float("nan"),
                "entity_type": "client_asn",
                "rows": [],
            }
        )
        with mock.patch("sys.argv", ["scorecard.py", payload]):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                exit_code = self.scorecard.main()

        document = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            document["errors"][0]["code"], "scorecard_trusted_context_missing"
        )
        self.assertIsNone(document["errors"][0]["details"]["scorecard_export_safe"])
        self.assertEqual(stderr.getvalue(), "")

    def test_scorecard_rejects_scorecard_input_until_trusted_handoff_exists(self) -> None:
        with self.assertRaises(self.scorecard.InvalidScorecardInputError) as exc:
            self.scorecard.build_artifacts(
                {
                    "schema_version": "bot_scorecard_input.v1",
                    "scorecard_export_safe": True,
                    "scorecard_handoff_evidence": {
                        "result_digest": "sha256:" + "1" * 64,
                    },
                    "entity_type": "client_asn",
                    "rows": [
                        {
                            "client_asn": "64500",
                            "current_requests": 1500,
                            "baseline_requests": 500,
                            "contribution_pct": 80,
                        }
                    ],
                },
                scorecard_trusted_context={"trusted_scorecard_handoff": True},
            )

        self.assertEqual(
            exc.exception.document["errors"][0]["code"],
            "scorecard_trusted_context_invalid",
        )

    def test_scorecard_main_emits_typed_error_for_advanced_input(self) -> None:
        payload = json.dumps(
            {
                "schema_version": "bot_attribution_report.v1",
                "dimensions": ["client_asn"],
                "movers": [],
            }
        )

        with mock.patch("sys.argv", ["scorecard.py"]):
            with mock.patch("sys.stdin", new=io.StringIO(payload)):
                with mock.patch("sys.stdout", new=io.StringIO()) as stdout:
                    exit_code = self.scorecard.main()

        document = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(document["schema_version"], "bot_scorecard_error.v1")
        self.assertEqual(
            document["errors"][0]["code"],
            "advanced_attribution_report_not_scorecard_input",
        )

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

    def test_scorecard_policy_collateral_features(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "bot_siem_summary_hour",
                "rows": [
                    {
                        "request_host": "www.example.com",
                        "current_requests": 5000,
                        "baseline_requests": 4000,
                        "good_bot_collateral_429_requests": 42,
                        "policy_collateral_error_rate_pct": 7,
                        "current_displacement_requests": 650,
                        "baseline_displacement_requests": 250,
                        "siem_blocked_requests": 100,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        features = {feature["name"]: feature for feature in card["features"]}
        self.assertEqual(card["domain_scores"]["policy_collateral"], 40)
        self.assertEqual(card["primary_domain"], "policy_collateral")
        self.assertIn("good_bot_policy_collateral_present", features)
        self.assertIn("policy_collateral_error_rate_high", features)
        self.assertIn("displacement_delta_high", features)
        self.assertIn(
            "Review collateral and displacement checks before declaring the policy change successful.",
            card["recommended_next_steps"],
        )

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

    def test_scorecard_sanitizes_non_finite_metadata_values(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "bot_class",
                "comparison_type": float("nan"),
                "granularity": float("inf"),
                "table_used": float("-inf"),
                "scope": {"request_host": "www.example.com", "sample_rate": float("nan")},
                "current_window": {"start": "2026-04-01", "weight": float("inf")},
                "baseline_windows": [
                    {
                        "start": "2026-03-25",
                        "end": "2026-04-01",
                        "weight": float("-inf"),
                    }
                ],
                "rowset_scope": {
                    "population": "all_traffic",
                    "filters": {"sampling_rate": float("nan")},
                },
                "feature_provenance": {
                    "rate_429_delta_high": {
                        "rowset_scope": {
                            "population": "all_traffic",
                            "filters": {"sampling_rate": float("inf")},
                        },
                        "metric_inputs": [
                            "current_rate_429_pct",
                            "baseline_rate_429_pct",
                        ],
                        "observed_weight": float("-inf"),
                    }
                },
                "rows": [
                    {
                        "bot_class": "bad",
                        "current_requests": 5000,
                        "baseline_requests": 1000,
                        "current_rate_429_pct": 12,
                        "baseline_rate_429_pct": 1,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertIsNone(card["comparison_type"])
        self.assertIsNone(card["granularity"])
        self.assertIsNone(card["table_used"])
        self.assertIn("raw_table_fallback", card["confidence_reasons"])
        self.assertNotIn("summary_table_used", card["confidence_reasons"])
        self.assertNotIn("retained_dimensions_fit", card["confidence_reasons"])
        self.assertIsNone(card["scope"]["sample_rate"])
        self.assertIsNone(card["current_window"]["weight"])
        self.assertIsNone(card["baseline_windows"][0]["weight"])
        self.assertIsNone(card["rowset_scope"]["filters"]["sampling_rate"])
        provenance = card["feature_provenance"]["rate_429_delta_high"]
        self.assertIsNone(provenance["rowset_scope"]["filters"]["sampling_rate"])
        self.assertIsNone(provenance["observed_weight"])
        self.assertIsNone(result["index"]["scope"]["sample_rate"])
        self.assertIsNone(result["index"]["comparison_type"])
        self.assertIsNone(result["index"]["table_used"])
        json.dumps(result, allow_nan=False)

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
