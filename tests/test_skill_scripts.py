from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
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
