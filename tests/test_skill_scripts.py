from __future__ import annotations

import copy
import ast
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
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


class HydrolixCaptureScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capture_hydrolix_query = load_module(
            "capture_hydrolix_query",
            ROOT / "scripts/capture-hydrolix-query.py",
        )

    def test_appends_format_json_when_missing(self) -> None:
        self.assertEqual(
            self.capture_hydrolix_query.ensure_format_json("SELECT 1;"),
            "SELECT 1 FORMAT JSON",
        )
        self.assertEqual(
            self.capture_hydrolix_query.ensure_format_json("SELECT 1 FORMAT JSON"),
            "SELECT 1 FORMAT JSON",
        )

    def test_writes_rows_without_printing_row_data(self) -> None:
        response = {
            "data": [{"secret_value": "do-not-print"}],
            "rows": 1,
            "statistics": {"elapsed": 0.01},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "rows.json"
            argv = [
                "capture-hydrolix-query.py",
                "--cluster",
                "demo.trafficpeak.live",
                "--sql",
                "SELECT secret_value FROM akamai.example WHERE timestamp >= now() - INTERVAL 1 HOUR",
                "--output",
                str(output),
                "--shape",
                "rows",
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.capture_hydrolix_query,
                    "run_mux_export",
                    side_effect=lambda _cluster, _sql, out: (
                        Path(out).write_text(json.dumps(response)),
                        {
                            "cluster": "demo.trafficpeak.live",
                            "bytes": Path(out).stat().st_size,
                        },
                    )[1],
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.capture_hydrolix_query.main(), 0)

            self.assertEqual(json.loads(output.read_text()), response["data"])
            printed = stdout.getvalue()
            self.assertIn('"rows": 1', printed)
            self.assertIn('"cluster": "demo.trafficpeak.live"', printed)
            self.assertNotIn("do-not-print", printed)
            self.assertNotIn("secret_value", printed)

    def test_requires_time_predicate_for_custom_sql(self) -> None:
        args = SimpleNamespace(
            start=None,
            end=None,
            require_time_range=True,
            table_surface="auto",
            preset=None,
            time_column="auto",
            granularity="auto",
            database="akamai",
        )

        with self.assertRaises(SystemExit):
            self.capture_hydrolix_query.apply_time_window_to_sql("SELECT 1", args)

    def test_auto_granularity_uses_standard_report_thresholds(self) -> None:
        parse_time = self.capture_hydrolix_query.parse_time
        select = self.capture_hydrolix_query.selected_granularity
        start = parse_time("2026-05-01T00:00:00Z", label="start")

        self.assertEqual(
            select(
                start,
                parse_time("2026-05-01T02:59:00Z", label="end"),
                "auto",
                surface="posture",
            ),
            "minute",
        )
        self.assertEqual(
            select(
                start,
                parse_time("2026-05-01T03:00:00Z", label="end"),
                "auto",
                surface="posture",
            ),
            "hour",
        )
        self.assertEqual(
            select(
                start,
                parse_time("2026-05-02T23:59:00Z", label="end"),
                "auto",
                surface="siem-policy",
            ),
            "hour",
        )
        self.assertEqual(
            select(
                start,
                parse_time("2026-05-03T00:00:00Z", label="end"),
                "auto",
                surface="siem-policy",
            ),
            "day",
        )

    def test_standard_presets_use_summary_tables_and_time_bounds(self) -> None:
        args = SimpleNamespace(
            preset="posture-by-asn",
            start="2026-05-01T00:00:00Z",
            end="2026-05-01T02:30:00Z",
            granularity="auto",
            database="akamai",
            limit=25,
        )

        sql = self.capture_hydrolix_query.render_preset_sql(args)

        self.assertIn("FROM akamai.bi_summary_minute", sql)
        self.assertIn("WHERE reqTimeSec >=", sql)
        self.assertIn("reqTimeSec <", sql)
        self.assertNotIn("bot_detection", sql)

        args.preset = "siem-policy"
        args.end = "2026-05-03T00:00:00Z"
        sql = self.capture_hydrolix_query.render_preset_sql(args)

        self.assertIn("FROM akamai.bi_siem_policy_summary_day", sql)
        self.assertIn("WHERE timestamp >=", sql)
        self.assertIn("timestamp <", sql)
        self.assertNotIn("bot_detection", sql)


class BotInsightsCaptureScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capture = load_module(
            "bot_insights_capture",
            ROOT / "skills/bot-insights/scripts/bot_insights_capture.py",
        )

    def test_env_file_parsing_literal_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "demo.env"
            env_file.write_text(
                "\n".join(
                    [
                        "# comment",
                        "export HDX_HOSTNAME='demo.example.com'",
                        'HDX_USERNAME="analyst"',
                        "HDX_PASSWORD=literal-password",
                    ]
                ),
                encoding="utf-8",
            )

            parsed = self.capture.parse_env_file(env_file)

        self.assertEqual(parsed["HDX_HOSTNAME"], "demo.example.com")
        self.assertEqual(parsed["HDX_USERNAME"], "analyst")
        self.assertEqual(parsed["HDX_PASSWORD"], "literal-password")

    def test_op_reexec_decision_honors_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "demo.env"
            env_file.write_text(
                "HDX_PASSWORD=op://vault/item/password\n", encoding="utf-8"
            )

            with mock.patch.object(
                self.capture.shutil, "which", return_value="/usr/local/bin/op"
            ):
                self.assertTrue(self.capture.should_reexec_with_op(env_file, {}))
                self.assertFalse(
                    self.capture.should_reexec_with_op(
                        env_file, {self.capture.SENTINEL_ENV: "1"}
                    )
                )

            with mock.patch.object(self.capture.shutil, "which", return_value=None):
                self.assertFalse(self.capture.should_reexec_with_op(env_file, {}))

    def test_normalizes_query_url(self) -> None:
        self.assertEqual(
            self.capture.normalize_query_url("demo.example.com"),
            "https://demo.example.com/query/",
        )
        self.assertEqual(
            self.capture.normalize_query_url("http://demo.example.com/query"),
            "http://demo.example.com/query/",
        )
        self.assertEqual(
            self.capture.normalize_query_url("https://demo.example.com/root/"),
            "https://demo.example.com/root/query/",
        )

    def test_auth_selection_does_not_expose_secret_in_config(self) -> None:
        token_config = self.capture.build_query_config(
            {"HDX_HOSTNAME": "demo.example.com", "HYDROLIX_TOKEN": "token-secret"}
        )
        self.assertEqual(token_config.auth_mode, "bearer")
        self.assertEqual(token_config.headers["Authorization"], "Bearer token-secret")

        basic_config = self.capture.build_query_config(
            {
                "HDX_HOSTNAME": "demo.example.com",
                "HDX_USERNAME": "analyst",
                "HDX_PASSWORD": "password-secret",
                "HDX_INSECURE_TLS": "true",
            }
        )
        self.assertEqual(basic_config.auth_mode, "basic")
        self.assertFalse(basic_config.verify_tls)
        self.assertTrue(basic_config.headers["Authorization"].startswith("Basic "))
        self.assertNotIn("password-secret", basic_config.headers["Authorization"])

    def test_credential_detection_from_env_and_cluster_file(self) -> None:
        env_state = self.capture.credential_state(
            {"HYDROLIX_HOST": "demo.example.com", "HDX_TOKEN": "token-secret"}
        )
        self.assertTrue(env_state.configured)
        self.assertEqual(env_state.auth_mode, "bearer")

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "acme.env"
            env_file.write_text(
                "\n".join(
                    [
                        "HDX_HOSTNAME=acme.example.com",
                        "HDX_USERNAME=analyst",
                        "HDX_PASSWORD=password-secret",
                    ]
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(
                self.capture.os.environ,
                {"BOT_INSIGHTS_CLUSTER_DIR": tmpdir},
                clear=True,
            ):
                merged, path = self.capture.merged_environment("acme")

        file_state = self.capture.credential_state(merged, path)
        self.assertTrue(file_state.configured)
        self.assertEqual(file_state.auth_mode, "basic")
        self.assertEqual(file_state.env_file, str(env_file))

    def test_unresolved_op_values_are_not_configured_without_op_resolution(
        self,
    ) -> None:
        state = self.capture.credential_state(
            {
                "HDX_HOSTNAME": "demo.example.com",
                "HDX_TOKEN": "op://vault/item/token",
            }
        )

        self.assertFalse(state.configured)
        self.assertIn("HYDROLIX_TOKEN/HDX_TOKEN", state.unresolved_op)
        self.assertEqual(state.op_resolution, "unresolved")

    def test_missing_host_and_auth_builds_handoff_without_secrets(self) -> None:
        args = SimpleNamespace(
            cluster="demo",
            database="akamai",
            preset="posture-overview",
            start="2026-05-01T00:00:00Z",
            end="2026-05-02T00:00:00Z",
            granularity="day",
            limit=100,
            shape="clickhouse",
        )
        credentials = self.capture.credential_state({})
        packet = self.capture.build_handoff_packet(
            args,
            "SELECT 1 FROM akamai.bi_summary_day WHERE reqTimeSec >= now() FORMAT JSON",
            credentials,
            Path("/tmp/raw.json"),
        )
        encoded = json.dumps(packet)

        self.assertEqual(packet["schema_version"], "bot_hydrolix_mcp_query_request.v1")
        self.assertFalse(packet["credential_status"]["configured"])
        self.assertIn(
            "HYDROLIX_HOST/HDX_HOSTNAME", packet["credential_status"]["missing"]
        )
        self.assertIn("run_select_query", packet["instruction"])
        self.assertNotIn("password-secret", encoded)
        self.assertNotIn("token-secret", encoded)

    def test_appends_format_json_when_missing(self) -> None:
        self.assertEqual(
            self.capture.ensure_format_json("SELECT 1;"),
            "SELECT 1 FORMAT JSON",
        )
        self.assertEqual(
            self.capture.ensure_format_json("SELECT 1 FORMAT JSON"),
            "SELECT 1 FORMAT JSON",
        )

    def test_presets_use_summary_tables_and_time_bounds(self) -> None:
        args = SimpleNamespace(
            preset="posture-by-path",
            start="2026-05-01T00:00:00Z",
            end="2026-05-01T02:30:00Z",
            granularity="auto",
            database="akamai",
            limit=25,
        )

        sql = self.capture.render_preset_sql(args)

        self.assertIn("FROM akamai.bi_summary_minute", sql)
        self.assertIn("WHERE reqTimeSec >=", sql)
        self.assertIn("reqTimeSec <", sql)
        self.assertNotIn("bot_detection", sql)

        args.preset = "siem-policy"
        args.end = "2026-05-03T00:00:00Z"
        sql = self.capture.render_preset_sql(args)

        self.assertIn("FROM akamai.bi_siem_policy_summary_day", sql)
        self.assertIn("WHERE timestamp >=", sql)
        self.assertIn("timestamp <", sql)
        self.assertNotIn("bot_detection", sql)

    def test_guarded_sql_rejections(self) -> None:
        invalid = [
            "SELECT * FROM akamai.bi_summary_hour",
            "SELECT * FROM akamai.bi_summary_hour WHERE reqTimeSec >= {{start}}",
            "DELETE FROM akamai.bi_summary_hour WHERE reqTimeSec >= now()",
            "SELECT 1; SELECT 2",
        ]
        for sql in invalid:
            with self.subTest(sql=sql):
                with self.assertRaises(SystemExit):
                    self.capture.reject_invalid_sql(sql, require_time_range=True)

        self.capture.reject_invalid_sql(
            "SELECT 1 FROM akamai.bi_summary_hour WHERE reqTimeSec >= now() - INTERVAL 1 HOUR",
            require_time_range=True,
        )

    def test_output_shaping(self) -> None:
        response = {"data": [{"value": 1}], "rows": 1, "statistics": {"elapsed": 0.01}}

        self.assertEqual(self.capture.shape_output(response, "clickhouse"), response)
        self.assertEqual(self.capture.shape_output(response, "rows"), [{"value": 1}])

    def test_http_success_posts_sql_and_parses_json(self) -> None:
        class FakeResponse:
            status = 200
            headers = {"X-HDX-Query-Stats": json.dumps({"rows_read": 12})}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"data": [{"value": 1}], "rows": 1}).encode("utf-8")

        config = self.capture.QueryConfig(
            url="https://demo.example.com/query/",
            headers={"Authorization": "Bearer token-secret"},
            verify_tls=True,
            auth_mode="bearer",
        )
        with mock.patch.object(
            self.capture.urllib.request, "urlopen", return_value=FakeResponse()
        ) as urlopen:
            response, meta = self.capture.query_hydrolix("SELECT 1 FORMAT JSON", config)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://demo.example.com/query/")
        self.assertEqual(request.data, b"SELECT 1 FORMAT JSON")
        self.assertEqual(response["data"], [{"value": 1}])
        self.assertEqual(meta["status"], 200)

    def test_http_failure_raises_without_row_data(self) -> None:
        config = self.capture.QueryConfig(
            url="https://demo.example.com/query/",
            headers={"Authorization": "Bearer token-secret"},
            verify_tls=True,
            auth_mode="bearer",
        )
        error = urllib.error.HTTPError(
            "https://demo.example.com/query/",
            500,
            "server error",
            {},
            io.BytesIO(b"query failed"),
        )
        with mock.patch.object(
            self.capture.urllib.request, "urlopen", side_effect=error
        ):
            with self.assertRaisesRegex(SystemExit, "HTTP 500"):
                self.capture.query_hydrolix("SELECT 1 FORMAT JSON", config)

    def test_main_missing_credentials_prints_handoff_and_skips_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "raw.json"
            argv = [
                "bot_insights_capture.py",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--preset",
                "posture-overview",
                "--start",
                "2026-05-01T00:00:00Z",
                "--end",
                "2026-05-02T00:00:00Z",
                "--output",
                str(output),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.dict(
                    self.capture.os.environ,
                    {},
                    clear=True,
                ),
                mock.patch.object(
                    self.capture,
                    "query_hydrolix",
                    side_effect=AssertionError("should not query"),
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.capture.main(), self.capture.NEEDS_MCP_EXIT)

        packet = json.loads(stdout.getvalue())
        self.assertEqual(packet["schema_version"], "bot_hydrolix_mcp_query_request.v1")
        self.assertEqual(packet["target_raw_output_path"], str(output.resolve()))
        self.assertFalse(output.exists())

    def test_main_direct_http_execution_when_credentials_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "raw.json"
            argv = [
                "bot_insights_capture.py",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--sql",
                "SELECT period, requests FROM akamai.bi_summary_day WHERE reqTimeSec >= now() - INTERVAL 1 HOUR",
                "--output",
                str(output),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.dict(
                    self.capture.os.environ,
                    {"HDX_HOSTNAME": "demo.example.com", "HDX_TOKEN": "token-secret"},
                    clear=True,
                ),
                mock.patch.object(
                    self.capture,
                    "query_hydrolix",
                    return_value=(
                        {"data": [{"period": "current", "requests": 1}], "rows": 1},
                        {"status": 200, "headers": {}, "response_bytes": 50},
                    ),
                ) as query,
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.capture.main(), 0)

            self.assertEqual(json.loads(output.read_text())["rows"], 1)
            printed = json.loads(stdout.getvalue())
            self.assertEqual(printed["auth_mode"], "bearer")
            self.assertEqual(printed["rows"], 1)
            query.assert_called_once()


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
        cls.bot_insights_report = load_module(
            "bot_insights_report",
            ROOT / "skills/bot-insights/scripts/bot_insights_report.py",
        )

    def test_bot_insights_artifact_scripts_are_offline_only(self) -> None:
        scripts_dir = ROOT / "skills/bot-insights/scripts"
        script_paths = sorted(scripts_dir.glob("*.py"))
        artifact_script_paths = [
            path
            for path in script_paths
            if path.name not in {"bot_insights_report.py", "bot_insights_capture.py"}
        ]
        blocked_import_roots = {
            "clickhouse_connect",
            "clickhouse_driver",
            "http",
            "hydrolix",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        blocked_text = re.compile(
            r"\b("
            r"GRAFANA_TOKEN|GRAFANA_URL|HYDROLIX_HOST|HDX_HOSTNAME|"
            r"op\s+run|curl|run_select_query|get_table_info|"
            r"dashboards\.trafficpeak\.live|demo\.trafficpeak\.live"
            r")\b"
        )

        violations: list[str] = []
        for path in artifact_script_paths:
            source = path.read_text()
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in blocked_import_roots:
                            violations.append(f"{path.name}: imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        root = node.module.split(".", 1)[0]
                        if root in blocked_import_roots:
                            violations.append(
                                f"{path.name}: imports from {node.module}"
                            )

            for match in blocked_text.finditer(source):
                violations.append(f"{path.name}: contains {match.group(0)!r}")

        self.assertEqual([], violations)

    def test_bot_insights_report_invokes_skill_local_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "evidence.json"
            sample_dir = Path(tmpdir) / "sample"
            calls = []

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                calls.append((cmd, stdout_path, cwd))
                if "bot_insights_capture.py" in str(cmd[1]):
                    raw_path = Path(cmd[cmd.index("--output") + 1])
                    raw_path.write_text(
                        json.dumps(
                            {
                                "data": [
                                    {"period": "baseline", "requests": 100},
                                    {"period": "current", "requests": 150},
                                ],
                                "rows": 2,
                            }
                        ),
                        encoding="utf-8",
                    )
                    return json.dumps({"cluster": "demo", "rows": 2})
                if stdout_path is not None:
                    stdout_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "bot_posture_movement.v1",
                                "current_window": {
                                    "start": "2026-05-02T00:00:00Z",
                                    "end": "2026-05-03T00:00:00Z",
                                },
                                "baseline_windows": [
                                    {
                                        "start": "2026-05-01T00:00:00Z",
                                        "end": "2026-05-02T00:00:00Z",
                                    }
                                ],
                                "metrics": [
                                    {
                                        "name": "requests",
                                        "current": 150,
                                        "baseline": 100,
                                        "absolute_delta": 50,
                                        "pct_change": 50,
                                        "direction": "increase",
                                        "confidence": "medium",
                                    }
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                    return ""
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--mode",
                "evidence",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.bot_insights_report.main(), 0)
            output_doc = json.loads(output.read_text())

            first_cmd = calls[0][0]
            self.assertIn("bot_insights_capture.py", str(first_cmd[1]))
            self.assertNotIn("capture-hydrolix-query", " ".join(map(str, first_cmd)))
            self.assertEqual(output_doc["schema_version"], "bot_report_evidence.v1")

    def test_bot_insights_report_handoff_exits_with_needs_mcp_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "evidence.json"
            sample_dir = Path(tmpdir) / "sample"
            handoff = {
                "schema_version": "bot_hydrolix_mcp_query_request.v1",
                "cluster": "demo",
                "database": "akamai",
                "validated_sql": "SELECT 1 FROM akamai.bi_summary_day WHERE reqTimeSec >= now() FORMAT JSON",
                "target_raw_output_path": str(
                    sample_dir / "executive_posture-raw.json"
                ),
                "mcp": {
                    "server": "hydrolix_mux",
                    "tool": "run_select_query",
                    "arguments": {
                        "cluster": "demo",
                        "query": "SELECT 1 FROM akamai.bi_summary_day WHERE reqTimeSec >= now() FORMAT JSON",
                    },
                },
            }

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                if "bot_insights_capture.py" in str(cmd[1]):
                    self.assertIn(
                        self.bot_insights_report.NEEDS_MCP_EXIT, allowed_returncodes
                    )
                    return json.dumps(handoff)
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--mode",
                "evidence",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(
                    self.bot_insights_report.main(),
                    self.bot_insights_report.NEEDS_MCP_EXIT,
                )

        printed = json.loads(stdout.getvalue())
        self.assertEqual(printed["schema_version"], "bot_hydrolix_mcp_query_request.v1")
        self.assertEqual(printed["mcp"]["tool"], "run_select_query")
        self.assertEqual(printed["mcp"]["arguments"]["cluster"], "demo")
        self.assertEqual(
            printed["mcp"]["arguments"]["query"],
            printed["validated_sql"],
        )
        self.assertEqual(
            printed["report_context"]["report"],
            "executive_posture",
        )
        self.assertEqual(printed["report_context"]["mode"], "evidence")
        self.assertEqual(
            printed["report_context"]["table_used"],
            "akamai.bi_summary_hour",
        )
        self.assertFalse(output.exists())

    def test_bot_insights_report_raw_input_resumes_without_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "evidence.json"
            sample_dir = Path(tmpdir) / "sample"
            raw_input = Path(tmpdir) / "mcp-result.json"
            raw_input.write_text(
                json.dumps(
                    {
                        "data": [
                            {"period": "baseline", "requests": 100},
                            {"period": "current", "requests": 125},
                        ],
                        "rows": 2,
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                calls.append(cmd)
                if "bot_insights_capture.py" in str(cmd[1]):
                    raise AssertionError("capture should be skipped")
                if stdout_path is not None:
                    stdout_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "bot_posture_movement.v1",
                                "current_window": {
                                    "start": "2026-05-02T00:00:00Z",
                                    "end": "2026-05-03T00:00:00Z",
                                },
                                "baseline_windows": [
                                    {
                                        "start": "2026-05-01T00:00:00Z",
                                        "end": "2026-05-02T00:00:00Z",
                                    }
                                ],
                                "metrics": [
                                    {
                                        "name": "requests",
                                        "current": 125,
                                        "baseline": 100,
                                        "absolute_delta": 25,
                                        "pct_change": 25,
                                        "direction": "increase",
                                        "confidence": "medium",
                                    }
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                    return ""
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--mode",
                "template",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
                "--raw-input",
                str(raw_input),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.bot_insights_report.main(), 0)

            self.assertIn("# Bot & Edge Movement", output.read_text())
            self.assertTrue(calls)
            self.assertNotIn("bot_insights_capture.py", " ".join(map(str, calls)))

    def test_bot_insights_report_control_review_evidence_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "control-evidence.json"
            sample_dir = Path(tmpdir) / "sample"
            calls = []

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                calls.append((cmd, stdout_path, cwd))
                joined = " ".join(map(str, cmd))
                if "bot_insights_capture.py" in joined:
                    self.assertIn("akamai.bi_siem_policy_summary_hour", joined)
                    self.assertIn("policy-123", joined)
                    raw_path = Path(cmd[cmd.index("--output") + 1])
                    raw_path.write_text(
                        json.dumps(
                            {
                                "data": [
                                    {
                                        "period": "before",
                                        "requests": 1000,
                                        "siem_blocked_requests": 90,
                                        "siem_auth_fail_requests": 10,
                                    },
                                    {
                                        "period": "after",
                                        "requests": 900,
                                        "siem_blocked_requests": 130,
                                        "siem_auth_fail_requests": 20,
                                    },
                                ],
                                "rows": 2,
                            }
                        ),
                        encoding="utf-8",
                    )
                    return json.dumps({"cluster": "demo", "rows": 2})
                if stdout_path is not None:
                    self.assertIn("--schema control", joined)
                    stdout_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "bot_control_review.v1",
                                "change_time": "2026-05-02T00:00:00Z",
                                "target": {"policy_id": "policy-123"},
                                "before_window": {
                                    "start": "2026-05-01T00:00:00Z",
                                    "end": "2026-05-02T00:00:00Z",
                                },
                                "after_window": {
                                    "start": "2026-05-02T00:00:00Z",
                                    "end": "2026-05-03T00:00:00Z",
                                },
                                "expected_basis": "before_window",
                                "target_effects": [
                                    {
                                        "metric": "siem_blocked_requests",
                                        "before": 90,
                                        "after": 130,
                                        "expected": 90,
                                        "absolute_delta_vs_expected": 40,
                                        "pct_change_vs_expected": 44.4444,
                                        "direction": "increase",
                                        "status": "increased",
                                        "confidence": "medium",
                                    }
                                ],
                                "collateral_checks": [],
                                "displacement_checks": [],
                            }
                        ),
                        encoding="utf-8",
                    )
                    return ""
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--report",
                "control_review",
                "--policy-id",
                "policy-123",
                "--mode",
                "evidence",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.bot_insights_report.main(), 0)

            packet = json.loads(output.read_text())
            self.assertEqual(packet["schema_version"], "bot_report_evidence.v1")
            self.assertEqual(packet["report_type"], "control_review")
            self.assertEqual(packet["target"]["policy_id"], "policy-123")
            self.assertEqual(
                packet["target_effects"][0]["metric"], "siem_blocked_requests"
            )
            self.assertEqual(
                packet["metric_cards"][0]["label"], "SIEM blocked requests"
            )
            self.assertTrue(
                any(
                    rate["name"] == "bot_like_share_pct"
                    for rate in packet["derived_rates"]
                )
            )
            self.assertIn(
                "Do not query Hydrolix",
                " ".join(packet["interpretation_contract"]["forbidden"]),
            )
            self.assertTrue(calls)

    def test_bot_insights_report_control_review_handoff_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "control-evidence.json"
            sample_dir = Path(tmpdir) / "sample"
            handoff = {
                "schema_version": "bot_hydrolix_mcp_query_request.v1",
                "cluster": "demo",
                "database": "akamai",
                "validated_sql": (
                    "SELECT 1 FROM akamai.bi_siem_policy_summary_day "
                    "WHERE timestamp >= now() FORMAT JSON"
                ),
                "target_raw_output_path": str(sample_dir / "control_review-raw.json"),
                "mcp": {
                    "server": "hydrolix_mux",
                    "tool": "run_select_query",
                    "arguments": {
                        "cluster": "demo",
                        "query": "SELECT 1 FROM akamai.bi_siem_policy_summary_day WHERE timestamp >= now() FORMAT JSON",
                    },
                },
            }

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                if "bot_insights_capture.py" in " ".join(map(str, cmd)):
                    self.assertIn(
                        self.bot_insights_report.NEEDS_MCP_EXIT, allowed_returncodes
                    )
                    return json.dumps(handoff)
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--report",
                "control_review",
                "--mode",
                "evidence",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-05T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(
                    self.bot_insights_report.main(),
                    self.bot_insights_report.NEEDS_MCP_EXIT,
                )

            printed = json.loads(stdout.getvalue())
            self.assertEqual(
                printed["schema_version"], "bot_hydrolix_mcp_query_request.v1"
            )
            self.assertEqual(printed["mcp"]["tool"], "run_select_query")
            self.assertEqual(printed["report_context"]["report"], "control_review")
            self.assertEqual(
                printed["report_context"]["table_used"],
                "akamai.bi_siem_policy_summary_day",
            )
            self.assertFalse(output.exists())

    def test_bot_insights_report_control_review_raw_input_builds_wrapper_for_render(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "control.html"
            sample_dir = Path(tmpdir) / "sample"
            raw_input = Path(tmpdir) / "mcp-result.json"
            raw_input.write_text(
                json.dumps(
                    {
                        "data": [
                            {"period": "before", "siem_blocked_requests": 90},
                            {"period": "after", "siem_blocked_requests": 130},
                        ],
                        "rows": 2,
                    }
                ),
                encoding="utf-8",
            )
            wrapper_seen = {}

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                joined = " ".join(map(str, cmd))
                if "bot_insights_capture.py" in joined:
                    raise AssertionError("capture should be skipped")
                if stdout_path is not None:
                    stdout_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "bot_control_review.v1",
                                "target": {"policy_scope": "all_policies"},
                                "before_window": {
                                    "start": "2026-05-01T00:00:00Z",
                                    "end": "2026-05-02T00:00:00Z",
                                },
                                "after_window": {
                                    "start": "2026-05-02T00:00:00Z",
                                    "end": "2026-05-03T00:00:00Z",
                                },
                                "expected_basis": "before_window",
                                "target_effects": [
                                    {
                                        "metric": "siem_blocked_requests",
                                        "before": 90,
                                        "after": 130,
                                        "expected": 90,
                                        "absolute_delta_vs_expected": 40,
                                        "pct_change_vs_expected": 44.4444,
                                        "status": "increased",
                                    }
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                    return ""
                if "render_report.py" in joined:
                    wrapper_path = Path(cmd[cmd.index("--file") + 1])
                    wrapper_seen.update(json.loads(wrapper_path.read_text()))
                    Path(cmd[cmd.index("--output") + 1]).write_text(
                        "<html>ok</html>", encoding="utf-8"
                    )
                    return ""
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--report",
                "control_review",
                "--mode",
                "report",
                "--format",
                "html",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
                "--raw-input",
                str(raw_input),
                "--analyst-notes",
                "Blocked requests increased relative to the before-window expectation.",
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.bot_insights_report.main(), 0)

            self.assertEqual(wrapper_seen["schema_version"], "bot_report_input.v1")
            self.assertEqual(wrapper_seen["report_type"], "control_review")
            self.assertEqual(
                wrapper_seen["artifacts"][0]["schema_version"], "bot_control_review.v1"
            )
            self.assertEqual(wrapper_seen["analyst_notes"][0]["author_type"], "llm")
            self.assertFalse(wrapper_seen["analyst_notes"][0]["show_data_sources"])
            self.assertEqual(output.read_text(), "<html>ok</html>")

    def test_bot_insights_report_scorecard_evidence_packet(self) -> None:
        artifacts = {
            "schema_version": "bot_scorecard_artifacts.v1",
            "producer_limit": 20,
            "result_row_count": 2,
            "index": {
                "schema_version": "bot_scorecard_index.v1",
                "ranked_entities": [
                    {
                        "rank": 1,
                        "entity_type": "request_host",
                        "entity": "www.example.com",
                        "score": 44,
                        "band": "medium_review",
                        "primary_domain": "cache_busting",
                        "confidence": "medium",
                    }
                ],
            },
        }
        card = {
            "schema_version": "bot_entity_scorecard.v1",
            "entity_type": "request_host",
            "entity": "www.example.com",
            "score": 44,
            "band": "medium_review",
            "primary_domain": "cache_busting",
            "confidence": "medium",
            "confidence_reasons": ["feature_input_missing"],
            "domain_scores": {"cache_busting": 18, "movement": 12},
            "features": [
                {
                    "domain": "cache_busting",
                    "name": "querystring_diversity_high",
                    "points": 16,
                    "evidence": "Query-string diversity ratio is 0.8.",
                }
            ],
            "not_evaluated_features": [
                {
                    "domain": "security_evidence",
                    "name": "siem_blocked_present",
                    "missing_inputs": ["siem_blocked_requests"],
                }
            ],
            "recommended_next_steps": ["Inspect query-string diversity."],
            "current_window": {
                "start": "2026-05-02T00:00:00Z",
                "end": "2026-05-03T00:00:00Z",
            },
            "baseline_windows": [
                {"start": "2026-05-01T00:00:00Z", "end": "2026-05-02T00:00:00Z"}
            ],
        }
        args = SimpleNamespace(
            report="scorecard_brief",
            title=None,
            cluster="demo",
            database="akamai",
            scorecard_limit=20,
            entity_value=None,
        )

        packet = self.bot_insights_report.build_scorecard_evidence_packet(
            args=args,
            artifacts=artifacts,
            selected_card=card,
            raw_path=Path("/tmp/raw.json"),
            artifact_path=Path("/tmp/artifact.json"),
            granularity="hour",
            table_used="akamai.bi_summary_hour",
            baseline_start=self.bot_insights_report.parse_time(
                "2026-05-01T00:00:00Z", "baseline-start"
            ),
        )

        self.assertEqual(packet["schema_version"], "bot_report_evidence.v1")
        self.assertEqual(packet["report_type"], "scorecard_brief")
        self.assertEqual(packet["selected_entity"]["rank"], 1)
        self.assertEqual(packet["domain_scores"]["cache_busting"], 18)
        self.assertEqual(packet["missing_inputs"], ["siem_blocked_requests"])
        forbidden = " ".join(packet["interpretation_contract"]["forbidden"])
        self.assertIn("Do not invent metrics", forbidden)
        self.assertIn("Do not query Hydrolix", forbidden)
        self.assertIn("Do not emit final HTML or Markdown", forbidden)

    def test_bot_insights_report_scorecard_handoff_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "scorecard-evidence.json"
            sample_dir = Path(tmpdir) / "sample"
            handoff = {
                "schema_version": "bot_hydrolix_mcp_query_request.v1",
                "cluster": "demo",
                "database": "akamai",
                "validated_sql": "SELECT 1 FROM akamai.bi_summary_hour WHERE reqTimeSec >= now() FORMAT JSON",
                "target_raw_output_path": str(sample_dir / "scorecard_brief-raw.json"),
                "mcp": {
                    "server": "hydrolix_mux",
                    "tool": "run_select_query",
                    "arguments": {
                        "cluster": "demo",
                        "query": "SELECT 1 FROM akamai.bi_summary_hour WHERE reqTimeSec >= now() FORMAT JSON",
                    },
                },
            }

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                if "bot_insights_capture.py" in " ".join(map(str, cmd)):
                    self.assertIn(
                        self.bot_insights_report.NEEDS_MCP_EXIT, allowed_returncodes
                    )
                    return json.dumps(handoff)
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--report",
                "scorecard_brief",
                "--mode",
                "evidence",
                "--entity-type",
                "request_host",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(
                    self.bot_insights_report.main(),
                    self.bot_insights_report.NEEDS_MCP_EXIT,
                )

            printed = json.loads(stdout.getvalue())
            self.assertEqual(
                printed["schema_version"], "bot_hydrolix_mcp_query_request.v1"
            )
            self.assertEqual(printed["report_context"]["report"], "scorecard_brief")
            self.assertEqual(
                printed["report_context"]["table_used"], "akamai.bi_summary_hour"
            )
            self.assertFalse(output.exists())

    def test_bot_insights_report_scorecard_raw_input_selects_top_ranked_entity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "scorecard-evidence.json"
            sample_dir = Path(tmpdir) / "sample"
            raw_input = Path(tmpdir) / "mcp-result.json"
            raw_input.write_text(
                json.dumps(
                    {
                        "data": [
                            {
                                "request_host": "www.example.com",
                                "current_requests": 200,
                                "baseline_requests": 50,
                            },
                            {
                                "request_host": "api.example.com",
                                "current_requests": 100,
                                "baseline_requests": 100,
                            },
                        ],
                        "rows": 2,
                    }
                ),
                encoding="utf-8",
            )

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                joined = " ".join(map(str, cmd))
                if "bot_insights_capture.py" in joined:
                    raise AssertionError("capture should be skipped")
                if "scorecard.py" in joined and stdout_path is not None:
                    stdout_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "bot_scorecard_artifacts.v1",
                                "scorecards": [
                                    {
                                        "schema_version": "bot_entity_scorecard.v1",
                                        "entity_type": "request_host",
                                        "entity": "www.example.com",
                                        "score": 55,
                                        "band": "medium_review",
                                        "primary_domain": "movement",
                                        "confidence": "high",
                                        "domain_scores": {"movement": 55},
                                        "features": [],
                                        "not_evaluated_features": [],
                                    },
                                    {
                                        "schema_version": "bot_entity_scorecard.v1",
                                        "entity_type": "request_host",
                                        "entity": "api.example.com",
                                        "score": 15,
                                        "band": "observe",
                                        "primary_domain": "none",
                                        "confidence": "high",
                                        "domain_scores": {"movement": 15},
                                        "features": [],
                                        "not_evaluated_features": [],
                                    },
                                ],
                                "index": {
                                    "schema_version": "bot_scorecard_index.v1",
                                    "ranked_entities": [
                                        {
                                            "rank": 1,
                                            "entity_type": "request_host",
                                            "entity": "www.example.com",
                                            "score": 55,
                                        },
                                        {
                                            "rank": 2,
                                            "entity_type": "request_host",
                                            "entity": "api.example.com",
                                            "score": 15,
                                        },
                                    ],
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                    return ""
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--report",
                "scorecard_brief",
                "--mode",
                "evidence",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
                "--raw-input",
                str(raw_input),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.bot_insights_report.main(), 0)

            packet = json.loads(output.read_text())
            self.assertEqual(packet["selected_entity"]["entity"], "www.example.com")
            self.assertEqual(packet["selected_entity"]["rank"], 1)

    def test_bot_insights_report_scorecard_report_selects_explicit_entity_and_wraps_notes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "scorecard.html"
            sample_dir = Path(tmpdir) / "sample"
            raw_input = Path(tmpdir) / "mcp-result.json"
            raw_input.write_text(json.dumps({"data": [], "rows": 0}), encoding="utf-8")
            wrapper_seen = {}

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                joined = " ".join(map(str, cmd))
                if "bot_insights_capture.py" in joined:
                    raise AssertionError("capture should be skipped")
                if "scorecard.py" in joined and stdout_path is not None:
                    stdout_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "bot_scorecard_artifacts.v1",
                                "scorecards": [
                                    {
                                        "schema_version": "bot_entity_scorecard.v1",
                                        "entity_type": "request_host",
                                        "entity": "www.example.com",
                                        "score": 55,
                                        "band": "medium_review",
                                        "primary_domain": "movement",
                                        "confidence": "high",
                                        "domain_scores": {"movement": 55},
                                        "features": [],
                                        "not_evaluated_features": [],
                                        "current_window": {
                                            "start": "2026-05-02T00:00:00Z",
                                            "end": "2026-05-03T00:00:00Z",
                                        },
                                        "baseline_windows": [
                                            {
                                                "start": "2026-05-01T00:00:00Z",
                                                "end": "2026-05-02T00:00:00Z",
                                            }
                                        ],
                                        "scope": {
                                            "cluster": "demo",
                                            "database": "akamai",
                                            "entity_type": "request_host",
                                        },
                                        "comparison_type": "previous_window",
                                        "table_used": "akamai.bi_summary_hour",
                                    },
                                    {
                                        "schema_version": "bot_entity_scorecard.v1",
                                        "entity_type": "request_host",
                                        "entity": "api.example.com",
                                        "score": 70,
                                        "band": "high_review",
                                        "primary_domain": "cache_busting",
                                        "confidence": "medium",
                                        "domain_scores": {"cache_busting": 70},
                                        "features": [],
                                        "not_evaluated_features": [],
                                        "current_window": {
                                            "start": "2026-05-02T00:00:00Z",
                                            "end": "2026-05-03T00:00:00Z",
                                        },
                                        "baseline_windows": [
                                            {
                                                "start": "2026-05-01T00:00:00Z",
                                                "end": "2026-05-02T00:00:00Z",
                                            }
                                        ],
                                        "scope": {
                                            "cluster": "demo",
                                            "database": "akamai",
                                            "entity_type": "request_host",
                                        },
                                        "comparison_type": "previous_window",
                                        "table_used": "akamai.bi_summary_hour",
                                    },
                                ],
                                "index": {
                                    "schema_version": "bot_scorecard_index.v1",
                                    "scope": {
                                        "cluster": "demo",
                                        "database": "akamai",
                                        "entity_type": "request_host",
                                    },
                                    "current_window": {
                                        "start": "2026-05-02T00:00:00Z",
                                        "end": "2026-05-03T00:00:00Z",
                                    },
                                    "baseline_windows": [
                                        {
                                            "start": "2026-05-01T00:00:00Z",
                                            "end": "2026-05-02T00:00:00Z",
                                        }
                                    ],
                                    "comparison_type": "previous_window",
                                    "table_used": "akamai.bi_summary_hour",
                                    "ranked_entities": [
                                        {
                                            "rank": 1,
                                            "entity_type": "request_host",
                                            "entity": "api.example.com",
                                            "score": 70,
                                        },
                                        {
                                            "rank": 2,
                                            "entity_type": "request_host",
                                            "entity": "www.example.com",
                                            "score": 55,
                                        },
                                    ],
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                    return ""
                if "render_report.py" in joined:
                    wrapper_path = Path(cmd[cmd.index("--file") + 1])
                    wrapper_seen.update(json.loads(wrapper_path.read_text()))
                    Path(cmd[cmd.index("--output") + 1]).write_text(
                        "<html>ok</html>", encoding="utf-8"
                    )
                    return ""
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--report",
                "scorecard_brief",
                "--mode",
                "report",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
                "--raw-input",
                str(raw_input),
                "--entity-type",
                "request_host",
                "--entity-value",
                "www.example.com",
                "--analyst-notes",
                "The selected host has movement evidence but should be reviewed with missing-input caveats.",
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.bot_insights_report.main(), 0)

            self.assertEqual(wrapper_seen["schema_version"], "bot_report_input.v1")
            self.assertEqual(wrapper_seen["report_type"], "scorecard_brief")
            self.assertEqual(wrapper_seen["artifacts"][0]["entity"], "www.example.com")
            self.assertEqual(
                wrapper_seen["artifacts"][1]["schema_version"], "bot_scorecard_index.v1"
            )
            self.assertEqual(
                wrapper_seen["analyst_notes"][0]["title"], "Scorecard Interpretation"
            )
            self.assertFalse(wrapper_seen["analyst_notes"][0]["show_data_sources"])

    def test_bot_insights_report_soc_triage_handoff_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "soc-evidence.json"
            sample_dir = Path(tmpdir) / "sample"
            handoff = {
                "schema_version": "bot_hydrolix_mcp_query_request.v1",
                "cluster": "demo",
                "database": "akamai",
                "validated_sql": "SELECT 1 FROM akamai.bi_siem_policy_summary_hour WHERE timestamp >= now() FORMAT JSON",
                "target_raw_output_path": str(sample_dir / "soc_triage-raw.json"),
                "mcp": {
                    "server": "hydrolix_mux",
                    "tool": "run_select_query",
                    "arguments": {
                        "cluster": "demo",
                        "query": "SELECT 1 FROM akamai.bi_siem_policy_summary_hour WHERE timestamp >= now() FORMAT JSON",
                    },
                },
            }
            captured_sql: dict[str, str] = {}

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                joined = " ".join(map(str, cmd))
                if "bot_insights_capture.py" in joined:
                    self.assertIn(
                        self.bot_insights_report.NEEDS_MCP_EXIT, allowed_returncodes
                    )
                    sql_index = cmd.index("--sql") + 1
                    captured_sql["sql"] = cmd[sql_index]
                    return json.dumps(handoff)
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--report",
                "soc_triage",
                "--mode",
                "evidence",
                "--entity-type",
                "request_host",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(
                    self.bot_insights_report.main(),
                    self.bot_insights_report.NEEDS_MCP_EXIT,
                )

            printed = json.loads(stdout.getvalue())
            self.assertEqual(
                printed["schema_version"], "bot_hydrolix_mcp_query_request.v1"
            )
            self.assertEqual(printed["report_context"]["report"], "soc_triage")
            self.assertEqual(
                printed["report_context"]["table_used"],
                "akamai.bi_siem_policy_summary_hour",
            )
            self.assertEqual(
                printed["report_context"]["analysis_domains"], "security_evidence"
            )
            self.assertIn("bi_siem_policy_summary_hour", captured_sql["sql"])
            self.assertIn("countIfMergeIf", captured_sql["sql"])
            self.assertFalse(output.exists())

    def test_bot_insights_report_soc_triage_raw_input_emits_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "soc-evidence.json"
            sample_dir = Path(tmpdir) / "sample"
            raw_input = Path(tmpdir) / "mcp-result.json"
            raw_input.write_text(
                json.dumps(
                    {
                        "data": [
                            {
                                "request_host": "www.example.com",
                                "current_requests": 200,
                                "baseline_requests": 50,
                                "siem_blocked_requests": 30,
                                "siem_auth_fail_requests": 12,
                            }
                        ],
                        "rows": 1,
                    }
                ),
                encoding="utf-8",
            )
            scorecard_invocations: list[list[str]] = []

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                joined = " ".join(map(str, cmd))
                if "bot_insights_capture.py" in joined:
                    raise AssertionError("capture should be skipped with --raw-input")
                if "scorecard.py" in joined and stdout_path is not None:
                    scorecard_invocations.append(list(cmd))
                    stdout_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "bot_scorecard_artifacts.v1",
                                "scorecards": [
                                    {
                                        "schema_version": "bot_entity_scorecard.v1",
                                        "entity_type": "request_host",
                                        "entity": "www.example.com",
                                        "score": 78,
                                        "band": "high_review",
                                        "primary_domain": "security_evidence",
                                        "confidence": "medium",
                                        "domain_scores": {"security_evidence": 78},
                                        "features": [
                                            {
                                                "name": "siem_blocked_present",
                                                "domain": "security_evidence",
                                                "evidence": "SIEM summary reports 30 blocked requests.",
                                            }
                                        ],
                                        "not_evaluated_features": [],
                                        "recommended_next_steps": [
                                            "Inspect SIEM block reasons."
                                        ],
                                    }
                                ],
                                "index": {
                                    "schema_version": "bot_scorecard_index.v1",
                                    "ranked_entities": [
                                        {
                                            "rank": 1,
                                            "entity_type": "request_host",
                                            "entity": "www.example.com",
                                            "score": 78,
                                        }
                                    ],
                                    "analysis_domains": ["security_evidence"],
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                    return ""
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--report",
                "soc_triage",
                "--mode",
                "evidence",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
                "--raw-input",
                str(raw_input),
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.bot_insights_report.main(), 0)

            self.assertEqual(len(scorecard_invocations), 1)
            scorecard_cmd = scorecard_invocations[0]
            self.assertIn("--domains", scorecard_cmd)
            domains_value = scorecard_cmd[scorecard_cmd.index("--domains") + 1]
            self.assertEqual(domains_value, "security_evidence")

            packet = json.loads(output.read_text())
            self.assertEqual(packet["schema_version"], "bot_report_evidence.v1")
            self.assertEqual(packet["report_type"], "soc_triage")
            self.assertEqual(packet["title"], "Bot Insights SOC Triage")
            self.assertEqual(
                packet["query_context"]["table_used"],
                "akamai.bi_siem_policy_summary_hour",
            )
            self.assertEqual(packet["selected_entity"]["entity"], "www.example.com")
            self.assertEqual(packet["selected_entity"]["rank"], 1)
            forbidden = " ".join(packet["interpretation_contract"]["forbidden"])
            self.assertIn("malicious", forbidden)
            allowed = " ".join(packet["interpretation_contract"]["allowed"])
            self.assertIn("SOC", allowed)
            self.assertIn("SOC Triage Summary", packet["template"]["sections"])

    def test_bot_insights_report_soc_triage_report_mode_renders_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "soc-triage.html"
            sample_dir = Path(tmpdir) / "sample"
            raw_input = Path(tmpdir) / "mcp-result.json"
            raw_input.write_text(json.dumps({"data": [], "rows": 0}), encoding="utf-8")
            wrapper_seen: dict = {}

            def fake_run(cmd, *, stdout_path=None, cwd=None, allowed_returncodes=()):
                joined = " ".join(map(str, cmd))
                if "bot_insights_capture.py" in joined:
                    raise AssertionError("capture should be skipped")
                if "scorecard.py" in joined and stdout_path is not None:
                    stdout_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "bot_scorecard_artifacts.v1",
                                "scorecards": [
                                    {
                                        "schema_version": "bot_entity_scorecard.v1",
                                        "entity_type": "request_host",
                                        "entity": "www.example.com",
                                        "score": 78,
                                        "band": "high_review",
                                        "primary_domain": "security_evidence",
                                        "confidence": "medium",
                                        "domain_scores": {"security_evidence": 78},
                                        "features": [],
                                        "not_evaluated_features": [],
                                        "scope": {
                                            "cluster": "demo",
                                            "database": "akamai",
                                            "entity_type": "request_host",
                                        },
                                        "comparison_type": "previous_window",
                                        "table_used": "akamai.bi_siem_policy_summary_hour",
                                        "current_window": {
                                            "start": "2026-05-02T00:00:00Z",
                                            "end": "2026-05-03T00:00:00Z",
                                        },
                                        "baseline_windows": [
                                            {
                                                "start": "2026-05-01T00:00:00Z",
                                                "end": "2026-05-02T00:00:00Z",
                                            }
                                        ],
                                    }
                                ],
                                "index": {
                                    "schema_version": "bot_scorecard_index.v1",
                                    "scope": {
                                        "cluster": "demo",
                                        "database": "akamai",
                                        "entity_type": "request_host",
                                    },
                                    "comparison_type": "previous_window",
                                    "table_used": "akamai.bi_siem_policy_summary_hour",
                                    "current_window": {
                                        "start": "2026-05-02T00:00:00Z",
                                        "end": "2026-05-03T00:00:00Z",
                                    },
                                    "baseline_windows": [
                                        {
                                            "start": "2026-05-01T00:00:00Z",
                                            "end": "2026-05-02T00:00:00Z",
                                        }
                                    ],
                                    "ranked_entities": [
                                        {
                                            "rank": 1,
                                            "entity_type": "request_host",
                                            "entity": "www.example.com",
                                            "score": 78,
                                        }
                                    ],
                                    "analysis_domains": ["security_evidence"],
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                    return ""
                if "render_report.py" in joined:
                    wrapper_path = Path(cmd[cmd.index("--file") + 1])
                    wrapper_seen.update(json.loads(wrapper_path.read_text()))
                    Path(cmd[cmd.index("--output") + 1]).write_text(
                        "<html>ok</html>", encoding="utf-8"
                    )
                    return ""
                raise AssertionError(cmd)

            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--report",
                "soc_triage",
                "--mode",
                "report",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(output),
                "--sample-dir",
                str(sample_dir),
                "--raw-input",
                str(raw_input),
                "--analyst-notes",
                "SIEM-active hosts cluster around www.example.com; review SIEM block reasons.",
            ]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    self.bot_insights_report, "run", side_effect=fake_run
                ),
                mock.patch("sys.stdout", stdout),
            ):
                self.assertEqual(self.bot_insights_report.main(), 0)

            self.assertEqual(wrapper_seen["schema_version"], "bot_report_input.v1")
            self.assertEqual(wrapper_seen["report_type"], "soc_triage")
            self.assertEqual(wrapper_seen["title"], "SOC Triage")
            schemas = [
                artifact["schema_version"] for artifact in wrapper_seen["artifacts"]
            ]
            self.assertEqual(schemas[0], "bot_scorecard_index.v1")
            self.assertIn("bot_entity_scorecard.v1", schemas)
            self.assertEqual(
                wrapper_seen["analyst_notes"][0]["title"],
                "SOC Triage Interpretation",
            )

    def test_bot_insights_report_soc_triage_rejects_path_entity_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            argv = [
                "bot-insights-report",
                "--cluster",
                "demo",
                "--database",
                "akamai",
                "--report",
                "soc_triage",
                "--mode",
                "evidence",
                "--entity-type",
                "request_path_norm",
                "--start",
                "2026-05-02T00:00:00Z",
                "--end",
                "2026-05-03T00:00:00Z",
                "--output",
                str(Path(tmpdir) / "soc-evidence.json"),
                "--sample-dir",
                str(Path(tmpdir) / "sample"),
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                self.assertRaises(SystemExit) as cm,
            ):
                self.bot_insights_report.main()
            self.assertIn("request_path_norm", str(cm.exception))

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
            summary_context={
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
            "table_used": "bi_summary_day",
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
                "selected_table": "bi_summary_day",
                "selected_columns": [
                    "timestamp",
                    "client_asn",
                    "request_host",
                    "sum(cnt_all)",
                ],
                "metadata_origin": "direct_hydrolix_table_metadata",
                "metadata_fingerprint": "sha256:" + "2" * 64,
                "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bi_summary_day:2026-04-02T00:00:00Z",
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

    def test_compare_delta_ignores_non_finite_numeric_values(self) -> None:
        result = self.compare_delta.compare(
            {
                "current": {"requests": float("nan"), "rate_429_pct": 3},
                "baseline": {"requests": 1, "rate_429_pct": 1},
            }
        )

        self.assertEqual([row["metric"] for row in result], ["rate_429_pct"])
        self.assertEqual(result[0]["pct_change"], 200)
        json.dumps(result, allow_nan=False)

    def test_baseline_scripts_load_sibling_helper_when_sys_path_is_shadowed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_baselines = Path(temp_dir) / "baselines.py"
            fake_baselines.write_text(
                "def to_number(value):\n"
                "    raise RuntimeError('shadowed baselines imported')\n"
                "def pct_delta(current, baseline):\n"
                "    raise RuntimeError('shadowed baselines imported')\n",
                encoding="utf-8",
            )
            with mock.patch.object(sys, "path", [temp_dir, *sys.path]):
                compare_delta = load_module(
                    "compare_delta_shadowed",
                    ROOT / "skills/bot-insights/scripts/compare_delta.py",
                )
                compare_posture = load_module(
                    "compare_posture_shadowed",
                    ROOT / "skills/bot-insights/scripts/compare_posture.py",
                )

        delta = compare_delta.compare(
            {"current": {"requests": 2}, "baseline": {"requests": 1}}
        )
        posture = compare_posture.compare(
            {
                "comparison_type": "previous_window",
                "granularity": "hour",
                "table_used": "bi_summary_hour",
                "current": {"requests": 2},
                "baseline": {"requests": 1},
            }
        )

        self.assertEqual(delta[0]["pct_change"], 100)
        self.assertEqual(posture["metrics"][0]["pct_change"], 100)

    def test_posture_movement_packet_from_mcp_rows(self) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "month_over_month",
                "granularity": "day",
                "table_used": "bi_summary_day",
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
                "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_day",
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
                "table_used": "bi_summary_hour",
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
                "scope": {
                    "request_host": "www.example.com",
                    "sample_rate": float("nan"),
                },
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
                "table_used": "bi_summary_day",
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

    def test_mover_uses_absolute_delta_denominator_for_unqualified_total_delta(
        self,
    ) -> None:
        result = self.compare_posture.compare(
            {
                "comparison_type": "week_over_week",
                "granularity": "day",
                "table_used": "bi_summary_day",
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
                "table_used": "bi_summary_day",
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
        self.assertEqual(result["total_delta_basis"], "complete_scope_total_abs_delta")
        self.assertEqual(result["movers"][0]["contribution_pct"], 20)
        self.assertEqual(result["movers"][1]["contribution_pct"], 10)

    def test_mover_artifact_preserves_compatibility_metadata(self) -> None:
        payload = {
            "comparison_type": "month_over_month",
            "granularity": "day",
            "table_used": "bi_summary_day",
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
                "scope": {
                    "request_host": "www.example.com",
                    "sample_rate": float("nan"),
                },
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
                "table_used": "bi_siem_policy_summary_day",
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
                "scope": {
                    "request_host": "www.example.com",
                    "sample_rate": float("inf"),
                },
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
                "table_used": "bi_summary_hour",
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
        self.assertEqual(
            result["movers"][0]["support_change_label"], "support_increase"
        )
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
        self.assertEqual(
            result["canonical_rows"][0]["dimensions"], {"client_asn": "64500"}
        )
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
        self.assertEqual(
            result["canonical_rows"][0]["dimensions"]["client_asn"], "64500"
        )
        self.assertEqual(result["baseline_method"], "single_previous_window")
        self.assertEqual(result["baseline_value_semantic"], "raw_total_window")

    def test_attribution_parser_exposes_required_options(self) -> None:
        args = self.attribution.parse_args(
            [
                "--metric",
                "requests",
                "--dimensions",
                "client_asn,bot_class",
                "--analysis",
                "policy_displacement",
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
        self.assertEqual(args.analysis, "policy_displacement")
        self.assertEqual(args.min_count, 75.0)
        self.assertEqual(args.limit, 5)
        self.assertEqual(args.output, "report")

    def test_attribution_policy_displacement_preserves_review_metadata(self) -> None:
        result = self.attribution.normalize_attribution(
            {
                "analysis_type": "policy_displacement",
                "metric": "requests",
                "dimensions": ["request_host"],
                "comparison_type": "post_policy_vs_baseline",
                "policy_change": {
                    "name": "block suspicious crawler policy",
                    "changed_at": "2026-04-15T12:00:00Z",
                },
                "target_effect": {
                    "metric": "blocked_requests",
                    "direction": "increase",
                },
                "table_used": "bi_summary_hour",
                "rows": [
                    {
                        "request_host": "api.example.com",
                        "current_requests": 700,
                        "baseline_requests": 300,
                    },
                    {
                        "request_host": "www.example.com",
                        "current_requests": 200,
                        "baseline_requests": 500,
                    },
                ],
            }
        )

        self.assertEqual(result["schema_version"], "bot_attribution_report.v1")
        self.assertEqual(result["analysis_type"], "policy_displacement")
        self.assertEqual(result["method"], "policy_displacement_attribution")
        self.assertEqual(
            result["policy_change"]["name"], "block suspicious crawler policy"
        )
        self.assertEqual(result["target_effect"]["metric"], "blocked_requests")
        self.assertIn("policy_displacement_review", result["confidence_reasons"])
        self.assertIn(
            "requires_external_policy_change_evidence",
            result["interpretation_constraints"],
        )
        summary = result["displacement_summary"]
        self.assertEqual(summary["increase_count"], 1)
        self.assertEqual(summary["decrease_count"], 1)
        self.assertEqual(summary["total_positive_delta"], 400)
        self.assertEqual(summary["total_negative_delta"], -300)
        self.assertEqual(summary["net_delta"], 100)
        self.assertEqual(
            summary["largest_increase"]["values"],
            {"request_host": "api.example.com"},
        )
        self.assertEqual(
            summary["largest_decrease"]["values"],
            {"request_host": "www.example.com"},
        )

    def test_attribution_cli_passes_policy_displacement_analysis(self) -> None:
        payload = json.dumps(
            {
                "rows": [
                    {
                        "request_host": "api.example.com",
                        "current_requests": 700,
                        "baseline_requests": 300,
                    }
                ]
            }
        )
        with mock.patch(
            "sys.argv",
            [
                "attribution.py",
                "--metric",
                "requests",
                "--dimensions",
                "request_host",
                "--analysis",
                "policy_displacement",
                payload,
            ],
        ):
            with mock.patch("sys.stdout", new=io.StringIO()) as stdout:
                exit_code = self.attribution.main()

        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["analysis_type"], "policy_displacement")
        self.assertEqual(result["method"], "policy_displacement_attribution")
        self.assertIn("displacement_summary", result)

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

    def test_attribution_excludes_unsafe_one_sided_rows_from_ranked_output(
        self,
    ) -> None:
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

    def test_attribution_withholds_contribution_for_assertion_only_metadata(
        self,
    ) -> None:
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

    def test_attribution_non_volume_metric_without_support_is_not_evaluated(
        self,
    ) -> None:
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
        self.assertIn(
            "lifecycle_support_missing", result["movers"][0]["confidence_reasons"]
        )
        self.assertEqual(
            result["not_evaluated_components"][1]["reason"],
            "lifecycle_support_missing",
        )

    def test_attribution_non_volume_metric_with_explicit_support_uses_lifecycle_labels(
        self,
    ) -> None:
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

    def test_attribution_additive_metric_prefers_explicit_support_for_lifecycle(
        self,
    ) -> None:
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

    def test_attribution_emits_sparse_candidate_for_low_support_one_sided_row(
        self,
    ) -> None:
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
        self.assertEqual(
            result["movers"][0]["candidate_flags"], ["sparse_new_candidate"]
        )
        self.assertEqual(result["buckets"]["not_evaluated_count"], 1)

    def test_attribution_zero_baseline_guard_uses_metric_math_not_lifecycle_absence(
        self,
    ) -> None:
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

    def test_attribution_scorecard_safe_assertions_do_not_raise_report_confidence(
        self,
    ) -> None:
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

    def test_attribution_summary_support_validates_single_dimension_tables(
        self,
    ) -> None:
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

    def test_attribution_summary_support_validates_composite_dimension_sets(
        self,
    ) -> None:
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
                "table_used": "bi_siem_policy_summary_hour",
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
        self.assertEqual(
            unsupported_components[0]["unsupported_columns"], ["client_asn"]
        )
        self.assertEqual(unsupported_components[0]["selected_table"], "bot_agg_hour")

    def test_attribution_rejects_unsupported_siem_dimension_and_filter(self) -> None:
        validation = self.attribution.validate_summary_table_support(
            "bi_siem_policy_summary_hour",
            ["akamai_canonical_bot_class"],
            filters={"hdx_cdn": "akamai"},
        )

        self.assertFalse(validation["supported"])
        self.assertEqual(
            validation["unsupported_grouped_dimensions"],
            ["akamai_canonical_bot_class"],
        )
        self.assertEqual(validation["unsupported_filter_columns"], ["hdx_cdn"])
        self.assertIn("unsupported_summary_dimension_set", validation["limitations"])
        self.assertIn("unsupported_summary_filter", validation["limitations"])

    def test_attribution_request_level_and_fixture_metadata_stay_below_high(
        self,
    ) -> None:
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
                "table_used": "bi_summary_hour",
                "summary_table_used": True,
                "generator_name": "bot-insights-attribution-sql",
                "metadata_fixture_identity": "fixture:bi_summary_hour:v1",
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
        self.assertIn("request_level_query", raw_result["confidence_reasons"])
        self.assertEqual(fixture_result["confidence"], "medium")
        self.assertIn(
            "trusted_context_reserved_for_future_tasks",
            fixture_result["confidence_reasons"],
        )
        self.assertNotEqual(raw_result["confidence"], "high")
        self.assertNotEqual(fixture_result["confidence"], "high")

    def test_attribution_result_digest_is_deterministic_and_rejects_nonfinite_values(
        self,
    ) -> None:
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
        context = self.trusted_context_for(
            input_doc, result_digest="sha256:" + "9" * 64
        )

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

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

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertIn("query_fingerprint_missing", result["confidence_reasons"])
        self.assertIn("result_digest_missing", result["confidence_reasons"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotEqual(result["confidence"], "high")

    def test_attribution_manual_origin_trusted_context_is_invalid(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        context["result_origin"] = "manual_paste"

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertIn("trusted_context_invalid", result["confidence_reasons"])
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertEqual(result["contribution_basis"], "none")

    def test_attribution_duplicate_evidence_ids_invalidate_trusted_context(
        self,
    ) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        duplicate = dict(context["trusted_evidence"][0])
        context["trusted_evidence"].append(duplicate)

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertIn("trusted_evidence_mismatch", result["confidence_reasons"])
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertEqual(result["contribution_basis"], "none")

    def test_attribution_evidence_contract_mismatch_degrades_to_no_contribution(
        self,
    ) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        context["trusted_evidence"][0]["metric"] = "blocked_requests"

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertIn("trusted_evidence_mismatch", result["confidence_reasons"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_incomplete_evidence_contract_is_invalid(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        context["trusted_evidence"][0].pop("baseline_value_semantic")

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertIn("trusted_evidence_mismatch", result["confidence_reasons"])
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertEqual(result["contribution_basis"], "none")

    def test_attribution_invalid_evidence_degrades_to_no_contribution(self) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)
        context["trusted_evidence"][0]["baseline_normalization"] = dict(
            context["trusted_evidence"][0]["baseline_normalization"]
        )
        context["trusted_evidence"][0]["baseline_normalization"]["factor"] = float(
            "nan"
        )

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertIn("trusted_evidence_mismatch", result["confidence_reasons"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotEqual(result["confidence"], "high")

    def test_attribution_valid_provided_contribution_fixture_remains_disabled_without_wrapper(
        self,
    ) -> None:
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

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertTrue(result["trusted_context_validation"]["valid"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertEqual(
            result["trusted_context_validation"]["evidence_types"],
            ["provided_contribution_evidence"],
        )
        self.assertIn("trusted_wrapper_unavailable", result["confidence_reasons"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_provided_contribution_evidence_requires_matching_field_identity(
        self,
    ) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.provided_contribution_context_for(input_doc)
        context["trusted_evidence"][0]["denominator_field"] = "caller_total_abs_delta"

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertIn(
            "provided_contribution_inconsistent", result["confidence_reasons"]
        )
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_invalid_provided_contribution_entry_invalidates_evidence_list(
        self,
    ) -> None:
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

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertIn(
            "provided_contribution_inconsistent", result["confidence_reasons"]
        )
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertEqual(
            result["trusted_context_validation"]["evidence_types"],
            ["complete_scope_pre_limit_evidence", "provided_contribution_evidence"],
        )
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_provided_contribution_evidence_requires_row_math_consistency(
        self,
    ) -> None:
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

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertIn(
            "provided_contribution_inconsistent", result["confidence_reasons"]
        )
        self.assertFalse(result["trusted_context_validation"]["valid"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertEqual(result["contribution_basis"], "none")
        self.assertNotIn("contribution_pct", result["movers"][0])

    def test_attribution_valid_future_wrapper_fixture_remains_disabled_without_wrapper(
        self,
    ) -> None:
        input_doc = self.trusted_attribution_input()
        context = self.trusted_context_for(input_doc)

        result = self.attribution.normalize_attribution(
            input_doc, trusted_context=context
        )

        self.assertTrue(result["trusted_context_validation"]["valid"])
        self.assertFalse(result["trusted_context_validation"]["trusted"])
        self.assertEqual(
            result["trusted_context_validation"]["evidence_types"],
            ["complete_scope_pre_limit_evidence"],
        )
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

    def test_attribution_duplicate_aggregation_evidence_does_not_unlock_local_aggregation(
        self,
    ) -> None:
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
            "table": "bi_summary_hour",
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
            "table": "bi_summary_hour",
        }

        first_hash = self.attribution.metadata_fingerprint(
            first,
            selected_columns=["request_host", "cnt_all"],
            metadata_fixture_identity="fixture:bi_summary_hour:v1",
        )
        second_hash = self.attribution.metadata_fingerprint(
            second,
            selected_columns=["cnt_all", "request_host"],
            metadata_fixture_identity="fixture:bi_summary_hour:v1",
        )

        self.assertEqual(first_hash, second_hash)
        self.assertTrue(first_hash.startswith("sha256:"))

    def test_attribution_sql_template_rejects_non_summary_metadata(self) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.render_attribution_sql_template(
                table_metadata={
                    "table": "bi_summary_hour",
                    "database": "bot_insights",
                    "is_summary_table": False,
                    "columns": [
                        {
                            "name": "timestamp",
                            "type": "DateTime",
                            "column_category": "Column",
                        },
                        {
                            "name": "request_host",
                            "type": "String",
                            "column_category": "Column",
                        },
                        {
                            "name": "client_asn",
                            "type": "String",
                            "column_category": "Column",
                        },
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

        self.assert_invalid_input_code(
            exc.exception, "table_metadata_not_summary_table"
        )

    def test_attribution_sql_template_uses_metadata_merge_expressions(self) -> None:
        table_metadata = {
            "table": "bi_summary_hour",
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
            metadata_fixture_identity="fixture:bi_summary_hour:v1",
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
        self.assertNotIn(
            "`timestamp` >= baseline_start_1\n      AND `timestamp` < current_end", sql
        )
        self.assertIn(
            "FULL OUTER JOIN baseline_by_entity AS b USING (`client_asn`)", sql
        )
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
            item
            for item in evidence
            if item["evidence_type"] == "complete_scope_pre_limit_evidence"
        ][0]
        zero_fill = [
            item for item in evidence if item["evidence_type"] == "zero_fill_evidence"
        ][0]
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
        self.assertFalse(
            any(value == "bot_scorecard_input.v1" for _, value in flattened)
        )

    def test_attribution_sql_template_supports_akamai_source_field_aliases(
        self,
    ) -> None:
        table_metadata = {
            "table": "bi_summary_day",
            "database": "akamai",
            "is_summary_table": True,
            "columns": [
                {
                    "name": "timestamp",
                    "type": "DateTime",
                    "column_category": "AliasColumn",
                },
                {"name": "reqHost", "type": "String", "column_category": "Column"},
                {"name": "asn", "type": "String", "column_category": "Column"},
                {"name": "country", "type": "String", "column_category": "Column"},
                {
                    "name": "count()",
                    "type": "AggregateFunction(count)",
                    "column_category": "AggregateColumn",
                    "merge_function": "countMerge",
                },
            ],
        }

        result = self.attribution.render_attribution_sql_template(
            table_metadata=table_metadata,
            metric="requests",
            dimensions=["client_asn"],
            scope={"request_host": "www.example.com"},
            filters={"client_country_iso_code": "US"},
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
            metadata_fixture_identity="fixture:akamai_bi_summary_day:v1",
        )

        sql = result["sql"]
        provenance = result["provenance"]

        self.assertIn("`asn` AS `client_asn`", sql)
        self.assertIn("`reqHost` = 'www.example.com'", sql)
        self.assertIn("`country` = 'US'", sql)
        self.assertIn(
            "FULL OUTER JOIN baseline_by_entity AS b USING (`client_asn`)", sql
        )
        self.assertIn("ORDER BY abs_delta DESC, toString(`client_asn`) ASC", sql)
        self.assertEqual(
            provenance["column_aliases"],
            {
                "client_asn": "asn",
                "client_country_iso_code": "country",
                "request_host": "reqHost",
            },
        )
        self.assertEqual(
            provenance["physical_sql_predicates"],
            {"country": "US", "reqHost": "www.example.com"},
        )
        self.assertIn("asn", provenance["selected_columns"])
        self.assertIn("reqHost", provenance["selected_columns"])
        self.assertIn("country", provenance["selected_columns"])
        self.assertEqual(
            provenance["requested_columns"],
            [
                "timestamp",
                "client_asn",
                "request_host",
                "client_country_iso_code",
                "count()",
            ],
        )

    def test_attribution_catalog_supports_bi_summary_tables(self) -> None:
        posture = self.attribution.validate_summary_table_support(
            "bi_summary_day",
            ["client_asn"],
            scope={"request_host": "www.example.com"},
            filters={"traffic_cohort": "AI"},
        )
        siem = self.attribution.validate_summary_table_support(
            "bi_siem_policy_summary_day",
            ["client_asn"],
            scope={"request_host": "www.example.com"},
            filters={"policy_id": "policy-1"},
        )

        self.assertTrue(posture["supported"])
        self.assertTrue(siem["supported"])
        self.assertIn("bi_summary_day", self.attribution.SUMMARY_TABLE_CATALOG)
        self.assertIn(
            "bi_siem_policy_summary_day", self.attribution.SUMMARY_TABLE_CATALOG
        )

    def test_attribution_sql_template_applies_filters_to_all_period_ctes(self) -> None:
        table_metadata = {
            "table": "bi_summary_hour",
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
        self.assertEqual(
            provenance["applied_scope_filters"], {"ai_category": ["search", "training"]}
        )
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

    def test_attribution_sql_template_rejects_conflicting_filter_predicates(
        self,
    ) -> None:
        table_metadata = {
            "table": "bi_summary_hour",
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

    def test_attribution_sql_template_multiple_baselines_are_explicit_and_deterministic(
        self,
    ) -> None:
        table_metadata = {
            "table": "bot_agg_path_day",
            "database": "bot_insights",
            "is_summary_table": True,
            "columns": [
                {"name": "timestamp", "type": "DateTime", "column_category": "Column"},
                {"name": "request_host", "type": "String", "column_category": "Column"},
                {
                    "name": "request_path_norm",
                    "type": "String",
                    "column_category": "Column",
                },
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

    def test_attribution_sql_template_mean_baseline_normalizes_average_window_duration(
        self,
    ) -> None:
        table_metadata = {
            "table": "bot_agg_path_day",
            "database": "bot_insights",
            "is_summary_table": True,
            "columns": [
                {"name": "timestamp", "type": "DateTime", "column_category": "Column"},
                {"name": "request_host", "type": "String", "column_category": "Column"},
                {
                    "name": "request_path_norm",
                    "type": "String",
                    "column_category": "Column",
                },
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

    def test_attribution_rejects_duplicate_period_split_entity_period_keys(
        self,
    ) -> None:
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

    def test_attribution_rejects_period_split_rows_with_combined_metric_aliases(
        self,
    ) -> None:
        with self.assertRaises(self.attribution.InvalidInputError) as exc:
            self.attribution.normalize_input_rows(
                {
                    "metric": "requests",
                    "dimensions": ["client_asn"],
                    "rows": [
                        {
                            "period": "current",
                            "client_asn": "64500",
                            "current_requests": 180,
                        },
                        {
                            "period": "baseline",
                            "client_asn": "64500",
                            "baseline_requests": 100,
                        },
                    ],
                }
            )

        self.assert_invalid_input_code(exc.exception, "no_usable_metric_values")

    def test_attribution_rejects_labeled_multi_baseline_rows_even_without_duplicates(
        self,
    ) -> None:
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

    def test_cache_origin_qs_semantics_aliases_control_exact_ratio_clamping(
        self,
    ) -> None:
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
        self.assertNotIn(
            "contribution_withheld_source_limited", candidate["limitations"]
        )
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

    def test_cache_origin_accepts_row_level_host_context_without_host_dimension(
        self,
    ) -> None:
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

    def test_cache_origin_requires_metric_semantics_for_contribution_denominator(
        self,
    ) -> None:
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
            trusted_context={"direct_mcp_trusted_context": True},
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
        self.assertEqual(
            sum(feature["points"] for feature in candidate["features"]), 105
        )
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

    def test_cache_origin_score_band_boundaries_and_high_miss_rate_threshold(
        self,
    ) -> None:
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

    def test_cache_origin_ranks_volume_sufficient_before_sparse_high_score(
        self,
    ) -> None:
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

    def test_cache_origin_source_limited_contribution_absence_lowers_confidence(
        self,
    ) -> None:
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

    def test_cache_origin_rowset_complete_contributions_are_computed_before_limit(
        self,
    ) -> None:
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
            candidate["share_denominators"][
                "current_total_cache_misses_for_contribution"
            ],
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

    def test_cache_origin_period_split_rows_preserve_current_contribution_fields(
        self,
    ) -> None:
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

        self.assertEqual(
            with_bytes["candidate_score"], without_bytes["candidate_score"]
        )
        self.assertFalse(
            without_bytes["optional_metadata"]["response_bytes"]["available"]
        )
        self.assertEqual(
            with_bytes["optional_metadata"]["response_bytes"],
            {"available": True, "current": 4096},
        )
        self.assertIn(
            "response_byte_metadata_not_available",
            without_bytes["limitations"],
        )

    def test_cache_origin_summary_context_is_host_scope_metadata(self) -> None:
        result = self.cache_origin_impact.build_report(
            self.cache_origin_payload(
                summary_context={
                    "scope": {"request_host": "www.example.com"},
                    "metrics": {
                        "host_bot_traffic_share_pct": 42.1,
                        "host_ai_category_share_pct": 7.4,
                    },
                }
            )
        )
        candidate = result["candidates"][0]
        context = candidate["optional_metadata"]["summary_context"]

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
            candidate["optional_metadata"]["summary_context"]["limitations"],
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
            candidate["share_denominators"][
                "current_total_cache_misses_for_contribution"
            ],
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
                "table_used": "bi_summary_hour",
                "rows": [
                    {
                        "client_asn": "64500",
                        "current_requests": 1500,
                        "baseline_requests": 500,
                        "current_bot_share_pct": 80,
                        "baseline_bot_share_pct": 40,
                        "current_cache_miss_pct": 60,
                        "baseline_cache_miss_pct": 40,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertEqual(card["schema_version"], "bot_entity_scorecard.v1")
        self.assertEqual(card["entity_type"], "client_asn")
        self.assertEqual(card["entity"], "64500")
        self.assertLess(card["score"], 100)
        self.assertIn("movement", card["domain_scores"])
        self.assertEqual(card["baseline_score"], 100)
        self.assertEqual(card["score_delta_points"], card["score"] - 100)
        self.assertIn("pre-baseline delta inputs", card["score_delta_basis"])

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

    def test_scorecard_rejects_self_attesting_scorecard_input_without_context(
        self,
    ) -> None:
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

    def test_scorecard_rejects_scorecard_input_until_trusted_handoff_exists(
        self,
    ) -> None:
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
                "table_used": "bi_summary_day",
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
        self.assertEqual(card["score"], 82)
        self.assertEqual(card["domain_scores"]["origin_impact"], 18)

    def test_scorecard_new_entity_zero_baseline_guard(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "client_asn",
                "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_hour",
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
                "table_used": "bi_siem_policy_summary_hour",
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
                "table_used": "bi_summary_day",
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
                "table_used": "bi_summary_day",
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
        rule_results = {rule["name"]: rule for rule in card["rule_results"]}
        self.assertEqual(rule_results["querystring_diversity_high"]["points"], 0)
        self.assertEqual(
            rule_results["querystring_diversity_high"]["status"], "missing_input"
        )
        self.assertEqual(rule_results["volume_delta_high"]["status"], "evaluated_zero")
        self.assertEqual(rule_results["volume_delta_high"]["points"], 0)
        self.assertIn("movement", card["domain_scores"])
        self.assertNotIn("security_evidence", card["domain_scores"])
        self.assertIn("feature_input_missing", card["confidence_reasons"])

    def test_scorecard_policy_collateral_features(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "bi_siem_policy_summary_hour",
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
        details = [step["detail"] for step in card["recommended_next_steps"]]
        self.assertIn(
            "Review collateral and displacement checks before declaring the policy change successful.",
            details,
        )

    def test_scorecard_policy_collateral_uses_available_protected_population_fields(
        self,
    ) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "analysis_domains": ["policy_collateral"],
                "table_used": "akamai.bi_summary_hour",
                "rows": [
                    {
                        "request_host": "docs.hydrolix.io",
                        "current_requests": 1243,
                        "baseline_requests": 1307,
                        "good_bot_429_requests": 0,
                        "good_bot_error_rate_pct": 0,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertEqual(card["analysis_domains"], ["policy_collateral"])
        self.assertEqual(card["domain_scores"]["policy_collateral"], 0)
        self.assertFalse(card["features"])
        missing_names = {feature["name"] for feature in card["not_evaluated_features"]}
        self.assertNotIn("good_bot_policy_collateral_present", missing_names)
        self.assertNotIn("policy_collateral_error_rate_high", missing_names)
        self.assertIn("displacement_delta_high", missing_names)
        displacement_rule = {rule["name"]: rule for rule in card["rule_results"]}[
            "displacement_delta_high"
        ]
        self.assertEqual(displacement_rule["status"], "missing_input")
        self.assertEqual(displacement_rule["points"], 0)
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
        self.assertLess(ranked[0]["score"], ranked[1]["score"])

    def test_scorecard_health_bands_follow_inverted_score(self) -> None:
        self.assertEqual(self.scorecard.score_band(0), "urgent_review")
        self.assertEqual(self.scorecard.score_band(20), "urgent_review")
        self.assertEqual(self.scorecard.score_band(21), "high_review")
        self.assertEqual(self.scorecard.score_band(40), "high_review")
        self.assertEqual(self.scorecard.score_band(41), "medium_review")
        self.assertEqual(self.scorecard.score_band(60), "medium_review")
        self.assertEqual(self.scorecard.score_band(61), "low_review")
        self.assertEqual(self.scorecard.score_band(80), "low_review")
        self.assertEqual(self.scorecard.score_band(81), "observe")
        self.assertEqual(self.scorecard.score_band(100), "observe")

    def test_scorecard_limit_metadata_when_truncated(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_day",
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

    def test_scorecard_crawler_governance_zero_inputs_are_evaluated(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "table_used": "akamai.bi_summary_hour",
                "rows": [
                    {
                        "request_host": "docs.hydrolix.io",
                        "current_requests": 1243,
                        "baseline_requests": 1307,
                        "current_rate_429_pct": 0,
                        "baseline_rate_429_pct": 0,
                        "current_rate_5xx_pct": 0,
                        "baseline_rate_5xx_pct": 0,
                        "current_ai_crawler_requests": 50,
                        "baseline_ai_crawler_requests": 84,
                        "good_bot_429_requests": 0,
                        "good_bot_error_rate_pct": 0,
                        "policy_surface_failures": 0,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        missing_names = {
            feature["name"]
            for feature in card["not_evaluated_features"]
            if feature["domain"] == "crawler_governance"
        }
        self.assertFalse(
            {
                "ai_crawler_growth_high",
                "good_bot_429_present",
                "good_bot_error_rate_high",
                "policy_surface_failure_present",
                "rate_429_delta_high",
                "rate_5xx_delta_high",
            }
            & missing_names
        )
        crawler_results = {
            rule["name"]: rule
            for rule in card["rule_results"]
            if rule["domain"] == "crawler_governance"
        }
        self.assertEqual(crawler_results["good_bot_429_present"]["points"], 0)
        self.assertEqual(
            crawler_results["good_bot_429_present"]["status"], "evaluated_zero"
        )
        self.assertEqual(
            crawler_results["rate_429_delta_high"]["status"], "evaluated_zero"
        )
        self.assertIn("summary_table_used", card["confidence_reasons"])

    def test_scorecard_soc_lens_uses_security_domain_only(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "request_host",
                "analysis_domains": ["security_evidence"],
                "table_used": "akamai.bi_siem_policy_summary_hour",
                "rows": [
                    {
                        "request_host": "akamai.appsec.work",
                        "current_requests": 500,
                        "baseline_requests": 300,
                        "siem_blocked_requests": 0,
                        "siem_auth_fail_requests": 12,
                        "bad_bot_share_pct": 0,
                    }
                ],
            }
        )

        card = result["scorecards"][0]
        self.assertEqual(card["analysis_domains"], ["security_evidence"])
        self.assertEqual(result["index"]["analysis_domains"], ["security_evidence"])
        self.assertEqual(card["primary_domain"], "security_evidence")
        self.assertEqual(card["domain_scores"]["security_evidence"], 12)
        self.assertEqual(card["features"][0]["name"], "siem_auth_fail_present")
        self.assertFalse(card["not_evaluated_features"])
        self.assertNotIn("feature_input_missing", card["confidence_reasons"])
        self.assertNotIn("siem_unavailable", card["confidence_reasons"])

    def test_scorecard_rejects_unknown_analysis_domain(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported domains"):
            self.scorecard.build_artifacts(
                {
                    "entity_type": "request_host",
                    "analysis_domains": ["security_evidence", "not_a_domain"],
                    "rows": [{"request_host": "www.example.com"}],
                }
            )

    def test_scorecard_includes_window_metadata(self) -> None:
        result = self.scorecard.build_artifacts(
            {
                "entity_type": "client_asn",
                "comparison_type": "week_over_week",
                "table_used": "bi_summary_hour",
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
                "scope": {
                    "request_host": "www.example.com",
                    "sample_rate": float("nan"),
                },
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
        self.assertIn("request_level_query", card["confidence_reasons"])
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
                    "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_day",
                "rowset_scope": {
                    "population": "good_bot",
                    "filters": {"bot_class": "good_bot"},
                    "entity_type": "bot_class",
                    "table_used": "bi_summary_day",
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
                "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_hour",
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
        with self.assertRaisesRegex(ValueError, "must not disagree on rowset_scope"):
            self.scorecard.build_artifacts(
                {
                    "entity_type": "client_asn",
                    "table_used": "bi_summary_hour",
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
                    "table_used": "bi_summary_hour",
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
                    "table_used": "bi_summary_hour",
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
                    "table_used": "bi_summary_hour",
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
                    "table_used": "bi_summary_hour",
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
                    "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_hour",
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

    def test_render_report_timeseries_alone_does_not_satisfy_report(self) -> None:
        with self.assertRaisesRegex(self.render_report.ReportError, "requires"):
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
            "table_used": "bi_summary_hour",
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
            "table_used": "bi_summary_hour",
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

    def test_render_report_executive_rolls_up_compatible_scorecards(self) -> None:
        shared = {
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "comparison_type": "previous_window",
            "table_used": "akamai.bi_summary_hour",
        }
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "executive_posture",
            "artifacts": [
                {
                    "schema_version": "bot_posture_movement.v1",
                    "artifact_id": "posture-1",
                    **shared,
                    "metrics": [
                        {
                            "name": "requests",
                            "current": 1500,
                            "baseline": 1000,
                            "absolute_delta": 500,
                            "pct_change": 50,
                            "direction": "increase",
                            "confidence": "high",
                        }
                    ],
                },
                {
                    "schema_version": "bot_scorecard_artifacts.v1",
                    "artifact_id": "scorecards-1",
                    "index": {
                        "schema_version": "bot_scorecard_index.v1",
                        **shared,
                        "ranked_entities": [
                            {
                                "rank": 1,
                                "entity_type": "request_host",
                                "entity": "www.example.com",
                                "score": 50,
                                "band": "medium_review",
                                "primary_domain": "cache_busting",
                                "confidence": "medium",
                            }
                        ],
                    },
                    "scorecards": [
                        {
                            "schema_version": "bot_entity_scorecard.v1",
                            **shared,
                            "entity_type": "request_host",
                            "entity": "www.example.com",
                            "score": 50,
                            "band": "medium_review",
                            "primary_domain": "cache_busting",
                            "domain_scores": {
                                "cache_busting": 40,
                                "origin_impact": 10,
                            },
                            "features": [],
                            "confidence_reasons": ["feature_input_missing"],
                        }
                    ],
                },
            ],
        }

        output, warnings = self.render_report.render(wrapper, self.render_args())

        self.assertIn("Lens Rollup", output)
        self.assertIn("Domain Totals", output)
        self.assertIn("| cache\\_busting | 40 |", output)
        self.assertIn("| origin\\_impact | 10 |", output)
        self.assertIn("Primary Lens Counts", output)
        self.assertIn("| cache\\_busting | 1 |", output)
        self.assertIn("Caveats", output)
        self.assertIn("| feature\\_input\\_missing | 1 |", output)
        self.assertIn("Domain Score Matrix", output)
        self.assertEqual(warnings, [])

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

    def test_render_report_scorecard_brief_html_includes_notes_timeline_visuals_and_compact_numbers(
        self,
    ) -> None:
        shared = {
            "scope": {
                "cluster": "demo",
                "database": "akamai",
                "entity_type": "request_host",
            },
            "current_window": {
                "start": "2026-05-02T00:00:00Z",
                "end": "2026-05-03T00:00:00Z",
            },
            "baseline_windows": [
                {"start": "2026-05-01T00:00:00Z", "end": "2026-05-02T00:00:00Z"}
            ],
            "comparison_type": "previous_window",
            "table_used": "akamai.bi_summary_hour",
        }
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "scorecard_brief",
            "title": "Scorecard Brief",
            "artifacts": [
                {
                    "schema_version": "bot_entity_scorecard.v1",
                    "artifact_id": "scorecard-1",
                    **shared,
                    "entity_type": "request_host",
                    "entity": "www.example.com",
                    "score": 90,
                    "baseline_score": 82,
                    "score_delta_points": 8,
                    "score_delta_basis": "Baseline score is recomputed from baseline-period point-in-time rule inputs; rules requiring pre-baseline delta inputs are excluded.",
                    "band": "high_review",
                    "primary_domain": "cache_busting",
                    "confidence": "medium",
                    "domain_scores": {"cache_busting": 58, "movement": 12},
                    "features": [
                        {
                            "domain": "cache_busting",
                            "name": "cache_miss_rate_high",
                            "points": 16,
                            "evidence": "Cache miss rate is 80%.",
                            "current": 80,
                            "baseline": 40,
                            "threshold": 50,
                            "supporting_metrics": {"absolute_delta_points": 40},
                        }
                    ],
                    "rule_results": [
                        {
                            "domain": "cache_busting",
                            "name": "cache_miss_rate_high",
                            "points": 16,
                            "status": "triggered",
                            "current": 80,
                            "baseline": 40,
                            "threshold": 50,
                            "supporting_metrics": {"absolute_delta_points": 40},
                        },
                        {
                            "domain": "cache_busting",
                            "name": "cache_miss_delta_high",
                            "points": 0,
                            "status": "evaluated_zero",
                            "current": 80,
                            "baseline": 78,
                            "threshold": 15,
                            "supporting_metrics": {"absolute_delta_points": 2},
                        },
                        {
                            "domain": "security_evidence",
                            "name": "siem_blocked_present",
                            "points": 0,
                            "status": "missing_input",
                            "missing_inputs": ["siem_blocked_requests"],
                            "reason": "feature_input_missing",
                        },
                    ],
                    "not_evaluated_features": [
                        {
                            "domain": "security_evidence",
                            "name": "siem_blocked_present",
                            "missing_inputs": ["siem_blocked_requests"],
                            "reason": "feature_input_missing",
                        }
                    ],
                    "recommended_next_steps": ["Inspect query-string diversity."],
                },
            ],
            "analyst_notes": [
                {
                    "author_type": "llm",
                    "title": "Scorecard Interpretation",
                    "text": "Review the selected host for cache-busting evidence.",
                    "show_data_sources": False,
                }
            ],
        }

        output, warnings = self.render_report.render(
            wrapper, self.render_args(format="html")
        )

        self.assertIn("<h2>Analyst Notes</h2>", output)
        self.assertIn('aria-label="Overall Score"', output)
        self.assertIn(
            '<text class="overall-gauge-metric" x="100" y="96" text-anchor="middle">90</text>',
            output,
        )
        self.assertIn('class="score-delta-up">+8 pts vs baseline</div>', output)
        self.assertLess(
            output.index('aria-label="Overall Score"'),
            output.index("<h2>Analyst Notes</h2>"),
        )
        self.assertLess(
            output.index('aria-label="Overall Score"'),
            output.index("<h2>Selected Entity Context</h2>"),
        )
        self.assertLess(
            output.index("<h2>Selected Entity Context</h2>"),
            output.index("<h2>Analyst Notes</h2>"),
        )
        self.assertLess(
            output.index('aria-label="Overall Score"'),
            output.index("Rule Score Matrix"),
        )
        self.assertLess(
            output.index("<h2>Analyst Notes</h2>"),
            output.index("Evidence Window Timeline"),
        )
        self.assertLess(
            output.index("Evidence Window Timeline"),
            output.index('aria-label="Charts"'),
        )
        self.assertNotIn("Scorecard Summary Cards", output)
        self.assertIn("<h2>Selected Entity Context</h2>", output)
        self.assertIn('aria-label="Selected Entity Context"', output)
        self.assertIn('class="entity-identity"', output)
        self.assertIn('<div class="entity-dimension">Request Host</div>', output)
        self.assertIn('<div class="entity-name">www.example.com</div>', output)
        self.assertIn("This brief explains the selected entity", output)
        self.assertIn('class="entity-metadata-row"', output)
        self.assertIn('<span class="entity-metadata-label">Rank</span>', output)
        self.assertIn("Unavailable", output)
        self.assertIn("Current Score", output)
        self.assertIn("Baseline Score", output)
        self.assertIn("Cache Busting", output)
        self.assertIn("Confidence", output)
        context_panel = output.split("<h2>Analyst Notes</h2>", 1)[0].split(
            "<h2>Selected Entity Context</h2>", 1
        )[1]
        self.assertIn("Unavailable", context_panel)
        self.assertNotIn(">Band</span>", context_panel)
        self.assertNotIn("entity-chip-row", output)
        self.assertNotIn("entity-chip-label", output)
        self.assertNotIn("entity-chip-value", output)
        self.assertNotIn('class="entity-chip"', output)
        self.assertIn("Security Evidence", output)
        self.assertIn("<th>Condition</th>", output)
        self.assertIn("<td>Cache Miss Rate</td><td>High</td>", output)
        self.assertIn("<td>SIEM Blocked Requests</td><td>Present</td>", output)
        self.assertIn("Feature Input Missing", output)
        self.assertNotIn("<td>cache_busting</td>", output)
        self.assertNotIn("<td>cache_miss_rate_high</td>", output)
        self.assertNotIn("<td>feature_input_missing</td>", output)
        self.assertNotIn('aria-label="Domain Scores"', output)
        self.assertIn("Domain Scores", output)
        self.assertIn("Rule Score Matrix", output)
        self.assertIn(
            'Cache Miss Rate</div><div class="rule-condition">High Increase</div>',
            output,
        )
        self.assertIn("80.00%", output)
        self.assertIn("^ 40.00%", output)
        self.assertIn("^ 2.00%", output)
        self.assertIn("Missing inputs", output)
        self.assertIn("0 pts", output)
        self.assertNotIn("prior 40.00%", output)
        self.assertNotIn("threshold 50.00%", output)
        visual = output.split("<h2>Domain Scores</h2>", 1)[0]
        self.assertNotIn("siem blocked present", visual)
        self.assertNotIn("N/A", visual)
        self.assertNotIn("Missing inputs", visual)
        self.assertNotIn("Cache miss rate is 80%.", visual)
        self.assertIn("Cache Miss Rate", visual)
        self.assertIn(
            'Cache Miss Rate</div><div class="rule-condition">High</div>', visual
        )
        self.assertIn(
            'Cache Miss Rate</div><div class="rule-condition">High Increase</div>',
            visual,
        )
        self.assertIn("-16 pts", visual)
        self.assertIn("0 pts", visual)
        self.assertIn('<svg class="rule-gauge"', visual)
        self.assertIn(
            '<text class="gauge-metric" x="60" y="57" text-anchor="middle">80.00%</text>',
            visual,
        )
        self.assertIn(
            '<text class="gauge-metric" x="60" y="57" text-anchor="middle">2.00%</text>',
            visual,
        )
        self.assertNotIn('class="rule-metric"', visual)
        self.assertNotIn('class="gauge-threshold"', visual)
        self.assertIn(
            'class="rule-status rule-status-triggered">triggered</div>', visual
        )
        self.assertIn("gauge-fill", visual)
        self.assertIn("Current Score", output)
        self.assertIn('<span class="entity-metadata-value">90</span>', output)
        self.assertIn("20", output)
        self.assertNotIn('{"start":', output)
        self.assertNotIn('"end":', output)
        self.assertEqual(warnings, [])

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
            "table_used": "bi_summary_day",
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
            "table_used": "bi_summary_hour",
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
            "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_hour",
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
                "table_used": "bi_summary_hour",
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
                    "table_used": "bi_summary_hour",
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
            "table_used": "bi_summary_hour",
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
                    "table_used": "bi_summary_hour",
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
            "table_used": "bi_summary_hour",
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
            "table_used": "bi_summary_hour",
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
            "table_used": "bi_summary_hour",
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
            "table_used": "bi_siem_policy_summary_day",
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
            "table_used": "bi_summary_day",
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
        self.assertIn('Target: \\{"policy\\_id": "policy\\`123\\`"\\}', output)
        self.assertIn("SIEM blocked requests", output)
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
                    "table_used": "bi_summary_hour",
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
        self.assertIn("- Table: bi\\_summary\\_hour", output)
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
        self.assertIn("evidence of movement only", output)
        self.assertNotIn("caused by", output)
        self.assertNotIn("proves", output)

    def test_render_report_soc_renders_missing_feature_evidence(self) -> None:
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx",
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "table_used": "bi_summary_hour",
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
            "table_used": "bi_summary_hour",
            "score": 80,
            "band": "urgent_review",
            "confidence": "medium",
            "confidence_reasons": ["sparse_counts"],
            "domain_scores": {"security_evidence": 80},
            "evidence_summary": [
                "Bad bot share crossed the review threshold.",
            ],
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
            "recommended_next_steps": [
                "Review SIEM policy actions for this ASN.",
            ],
        }
        output, _ = self.render_report.render(
            [index, scorecard], self.render_args(report_type="soc_triage")
        )
        self.assertIn("Scorecard Analysis", output)
        self.assertIn("Evidence Summary", output)
        self.assertIn("Bad bot share crossed the review threshold", output)
        self.assertIn("Evaluated Features", output)
        self.assertIn("Recommended Next Steps", output)
        self.assertIn("Review SIEM policy actions for this ASN", output)
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
        self.assertNotIn("Scorecard Analysis", output)
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
            "table_used": "bi_summary_hour",
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
            "table_used": "bi_summary_hour",
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
            "table_used": "bi_summary_hour",
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
            "table_used": "bi_summary_hour",
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

    def test_render_report_html_places_summary_before_charts(self) -> None:
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
        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))
        self.assertLess(
            output.index("Executive Summary"),
            output.index('aria-label="Charts"'),
        )

    def test_render_report_executive_summary_uses_metric_evidence(self) -> None:
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
                },
                {
                    "name": "cache_misses",
                    "current": 350,
                    "baseline": 100,
                    "absolute_delta": 250,
                    "pct_change": 250,
                    "direction": "increase",
                    "confidence": "high",
                },
                {
                    "name": "error_5xx_requests",
                    "current": 30,
                    "baseline": 10,
                    "absolute_delta": 20,
                    "pct_change": 200,
                    "direction": "increase",
                    "confidence": "medium",
                },
            ]
        )
        output, _ = self.render_report.render(wrapper, self.render_args())

        self.assertIn("Total requests increased by \\+500", output)
        self.assertIn("from 1\\.00K baseline to 1\\.50K current", output)
        self.assertIn("Largest relative movements: Cache misses \\+250\\.0%", output)
        self.assertIn("Operational signals to review: Cache misses \\+250", output)
        self.assertIn("does not identify root cause or malicious intent", output)

    def test_render_report_html_places_llm_notes_before_evidence(self) -> None:
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
        wrapper["analyst_notes"] = [
            {
                "note_id": "llm-summary",
                "author_type": "llm",
                "title": "Executive Interpretation",
                "text": "Request volume increased materially in the current window.",
                "data_sources": [
                    {
                        "artifact_id": "posture-1",
                        "json_pointer": "/metrics/0/pct_change",
                        "label": "request pct change",
                    }
                ],
            }
        ]

        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))

        self.assertLess(
            output.index("Executive Interpretation"),
            output.index("Executive Summary"),
        )
        self.assertLess(
            output.index("Executive Interpretation"),
            output.index('aria-label="Charts"'),
        )
        self.assertIn("request pct change: +50.0%", output)
        self.assertNotIn("/metrics/0/pct_change", output)

    def test_render_report_markdown_places_llm_notes_before_evidence(self) -> None:
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
        wrapper["analyst_notes"] = [
            {
                "note_id": "llm-summary",
                "author_type": "llm",
                "title": "Executive Interpretation",
                "text": "Request volume increased materially in the current window.",
                "show_data_sources": False,
                "data_sources": [],
            }
        ]

        output, _ = self.render_report.render(wrapper, self.render_args())

        self.assertLess(
            output.index("Executive Interpretation"),
            output.index("Executive Summary"),
        )

    def test_render_report_notes_do_not_drive_metric_or_chart_values(self) -> None:
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
        wrapper["analyst_notes"] = [
            {
                "note_id": "llm-summary",
                "author_type": "llm",
                "title": "Executive Interpretation",
                "text": "Requests are 999999 current and 1 baseline.",
                "show_data_sources": False,
                "data_sources": [],
            }
        ]

        output, _ = self.render_report.render(wrapper, self.render_args())
        evidence_output = output[output.index("## Executive Summary") :]

        self.assertIn("from 1\\.00K baseline to 1\\.50K current", evidence_output)
        self.assertIn("Requests are 999999 current", output)
        self.assertNotIn("999999", evidence_output)

    def test_render_report_html_omits_visible_report_metadata(self) -> None:
        wrapper = self._posture_wrapper(
            [
                {
                    "name": "requests",
                    "current": 1500,
                    "baseline": 1000,
                }
            ]
        )

        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))

        self.assertNotIn("Report type:", output)
        self.assertNotIn("Scope:", output)

    def test_render_report_html_renders_timeseries_trend_cards_before_summary(
        self,
    ) -> None:
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
        wrapper["artifacts"].append(
            {
                "schema_version": "bot_timeseries.v1",
                "artifact_id": "trend-1",
                "current_window": {
                    "start": "2026-04-01T00:00:00Z",
                    "end": "2026-04-08T00:00:00Z",
                },
                "baseline_windows": [
                    {"start": "2026-03-25T00:00:00Z", "end": "2026-04-01T00:00:00Z"}
                ],
                "metrics": [
                    {
                        "name": "requests",
                        "label": "Request volume",
                        "current": 1500,
                        "baseline": 1000,
                        "pct_change": 50,
                        "points": [
                            {
                                "timestamp": "2026-04-01T00:00:00Z",
                                "current": 500,
                                "baseline": 300,
                            },
                            {
                                "timestamp": "2026-04-01T01:00:00Z",
                                "current": 1000,
                                "baseline": 700,
                            },
                        ],
                    }
                ],
            }
        )

        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))

        self.assertIn("Posture Trend Cards", output)
        self.assertIn("Request volume", output)
        self.assertIn("Current 1.50K vs prior 1.00K", output)
        self.assertIn("Hourly trend evidence", output)
        self.assertIn("Trend cards: 1 hourly metric series", output)
        self.assertNotIn("Data source:", output)
        self.assertNotIn("current_window:", output)
        self.assertNotIn(
            "current window 2026-04-01 00:00 UTC to 2026-04-08 00:00 UTC", output
        )
        self.assertIn("Evidence Window Timeline", output)
        self.assertIn('aria-label="Evidence window timeline"', output)
        self.assertIn("Report comparison window", output)
        self.assertEqual(output.count(">Baseline</text>"), 1)
        self.assertEqual(output.count(">Current</text>"), 1)
        self.assertNotIn("Baseline: 2026-04-01", output)
        self.assertNotIn("Schema: bot_timeseries.v1", output)
        self.assertNotIn("Confidence: unavailable", output)
        self.assertLess(
            output.index('aria-label="Evidence window timeline"'),
            output.index('aria-label="Posture trend cards"'),
        )
        self.assertLess(
            output.index('aria-label="Posture trend cards"'),
            output.index("Executive Summary"),
        )

    def test_render_report_can_hide_visible_llm_note_citations(self) -> None:
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
        wrapper["analyst_notes"] = [
            {
                "note_id": "llm-summary",
                "author_type": "llm",
                "title": "Executive Interpretation",
                "text": "Request volume increased materially.",
                "show_data_sources": False,
                "data_sources": [
                    {
                        "artifact_id": "posture-1",
                        "json_pointer": "/metrics/0/pct_change",
                        "label": "request pct change",
                    }
                ],
            }
        ]

        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))

        self.assertIn("Executive Interpretation", output)
        self.assertNotIn("Supporting evidence", output)
        self.assertNotIn("request pct change", output)

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
        self.assertIn("<th>cache_busting</th>", output)
        self.assertNotIn("<th>security_evidence</th>", output)
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
        self.assertIn("SIEM blocked requests", output)
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
            "features": [
                {
                    "domain": "cache_busting",
                    "name": "cache_miss_rate_high",
                    "points": 10,
                    "current": 75,
                    "baseline": 60,
                    "threshold": 50,
                    "supporting_metrics": {"absolute_delta_points": 15},
                    "evidence": "Cache miss rate is 75%.",
                }
            ],
        }
        output, _ = self.render_report.render(
            scorecard,
            self.render_args(report_type="scorecard_brief", format="html"),
        )
        self.assertIn("Rule Score Matrix", output)
        self.assertNotIn('aria-label="Domain Scores"', output)
        self.assertIn("75.00%", output)
        self.assertIn("^ 15.00%", output)
        self.assertNotIn("threshold 50.00%", output)
        visual = output.split("<h2>Domain Scores</h2>", 1)[0]
        self.assertNotIn("Cache miss rate is 75%.", visual)
        self.assertIn(
            'Cache Miss Rate</div><div class="rule-condition">High</div>', visual
        )
        self.assertIn("-10 pts", visual)
        self.assertIn('<svg class="rule-gauge"', visual)
        self.assertIn(
            '<text class="gauge-metric" x="60" y="57" text-anchor="middle">75.00%</text>',
            visual,
        )
        self.assertNotIn('class="rule-metric"', visual)
        self.assertNotIn('class="gauge-threshold"', visual)
        self.assertIn(
            'class="rule-status rule-status-triggered">triggered</div>', visual
        )
        self.assertIn("gauge-fill", visual)

    def test_render_report_html_scorecard_brief_fleet_first_layout(self) -> None:
        alpha = {
            "schema_version": "bot_entity_scorecard.v1",
            "artifact_id": "alpha",
            "entity_type": "request_host",
            "entity": "alpha.example",
            "scope": {
                "cluster": "demo",
                "database": "akamai",
                "entity_type": "request_host",
            },
            "comparison_type": "previous_window",
            "table_used": "akamai.bi_summary_hour",
            "current_window": {
                "start": "2026-05-02T00:00:00Z",
                "end": "2026-05-03T00:00:00Z",
            },
            "baseline_windows": [
                {"start": "2026-05-01T00:00:00Z", "end": "2026-05-02T00:00:00Z"}
            ],
            "score": 64,
            "baseline_score": 68,
            "score_delta_points": -4,
            "band": "high_review",
            "primary_domain": "cache_busting",
            "confidence": "medium",
            "confidence_reasons": ["summary_table_used", "feature_input_missing"],
            "domain_scores": {"cache_busting": 30},
            "rule_results": [
                {
                    "domain": "cache_busting",
                    "name": "cache_miss_rate_high",
                    "status": "triggered",
                    "points": 10,
                    "evidence": "Cache miss rate is elevated.",
                },
                {
                    "domain": "cache_busting",
                    "name": "querystring_diversity_high",
                    "status": "evaluated_zero",
                    "points": 0,
                },
                {
                    "domain": "origin_impact",
                    "name": "origin_p95_delta_high",
                    "status": "missing_input",
                    "missing_inputs": ["current_origin_p95_ms"],
                },
            ],
            "evidence_summary": ["Cache miss rate is elevated."],
            "recommended_next_steps": ["Review cache-key behavior."],
            "interpretation_constraints": ["rule_based_scorecard", "no_causal_claim"],
        }
        beta = copy.deepcopy(alpha)
        beta.update(
            {
                "artifact_id": "beta",
                "entity": "beta.example",
                "score": 72,
                "baseline_score": 72,
                "score_delta_points": 0,
                "band": "observe",
                "primary_domain": "movement",
                "confidence": "low",
                "confidence_reasons": ["summary_table_used", "sparse_counts"],
                "rule_results": [
                    {
                        "domain": "cache_busting",
                        "name": "cache_miss_rate_high",
                        "status": "triggered",
                        "points": 8,
                        "evidence": "Cache miss rate crossed threshold.",
                    },
                    {
                        "domain": "movement",
                        "name": "volume_delta_high",
                        "status": "evaluated_zero",
                        "points": 0,
                    },
                ],
                "evidence_summary": ["Cache miss rate crossed threshold."],
                "recommended_next_steps": [
                    "Review cache-key behavior.",
                    "Confirm comparable current and baseline windows.",
                ],
            }
        )
        index = {
            "schema_version": "bot_scorecard_index.v1",
            "artifact_id": "idx",
            "scope": {
                "cluster": "demo",
                "database": "akamai",
                "entity_type": "request_host",
            },
            "comparison_type": "previous_window",
            "table_used": "akamai.bi_summary_hour",
            "current_window": alpha["current_window"],
            "baseline_windows": alpha["baseline_windows"],
            "producer_limit": 20,
            "result_row_count": 2,
            "total_ranked_entities": 2,
            "ranked_entities": [
                {
                    "rank": 1,
                    "entity_type": "request_host",
                    "entity": "beta.example",
                    "score": 72,
                    "primary_domain": "movement",
                    "confidence": "low",
                    "band": "observe",
                },
                {
                    "rank": 2,
                    "entity_type": "request_host",
                    "entity": "alpha.example",
                    "score": 64,
                    "primary_domain": "cache_busting",
                    "confidence": "medium",
                    "band": "high_review",
                },
            ],
            "interpretation_constraints": ["rule_based_scorecard"],
        }
        wrapper = {
            "schema_version": "bot_report_input.v1",
            "report_type": "scorecard_brief",
            "title": "Fleet Scorecard Brief",
            "analyst_notes": [
                {
                    "note_id": "note-1",
                    "author_type": "analyst",
                    "title": "Analyst Context",
                    "text": "Review cache behavior before taking action.",
                    "show_data_sources": False,
                }
            ],
            "artifacts": [alpha, beta, index],
        }

        output, _ = self.render_report.render(
            wrapper,
            self.render_args(report_type="scorecard_brief", format="html"),
        )

        self.assertIn("Fleet KPI Strip", output)
        self.assertLess(output.index("Fleet KPI Strip"), output.index("Analyst Notes"))
        self.assertLess(
            output.index("What This Report Says"), output.index("Analyst Notes")
        )
        self.assertIn(
            'Fleet Health Score</div><div class="fleet-kpi-value">68</div>', output
        )
        self.assertIn("May 2, 2026, 00:00-24:00 UTC", output)
        self.assertIn("May 1, 2026, 00:00-24:00 UTC", output)
        self.assertNotIn("2026-05-02 00:00 UTC to 2026-05-03 00:00 UTC", output)
        self.assertIn("2 of 2 entities have triggered scorecard rules", output)
        self.assertIn(
            "Most common triggered feature: Cache Miss Rate High across 2 entities.",
            output,
        )
        self.assertIn(
            "Missing-input coverage: 1 rule evaluations were unavailable across 3 domains.",
            output,
        )
        self.assertIn(
            "Score movement count: 1 entities have nonzero score_delta_points.", output
        )
        self.assertIn("<td>Cache Busting</td><td>2</td><td>1</td><td>0</td>", output)
        self.assertIn("<td>Origin Impact</td><td>0</td><td>0</td><td>1</td>", output)
        self.assertLess(output.index("beta.example"), output.index("alpha.example"))
        self.assertIn("Cache miss rate crossed threshold.", output)
        self.assertIn("Review cache-key behavior.</td><td>2</td>", output)
        self.assertIn(
            'Confidence Ceiling</div><div class="fleet-kpi-value">low</div>', output
        )
        self.assertIn("Method And Caveats", output)
        self.assertIn("sparse_counts", output)
        self.assertNotIn("Selected Entity Context", output)
        self.assertNotIn("Overall Score", output)
        self.assertNotIn("Scorecard Ranking Bars", output)
        self.assertNotIn("Band", output)

    def test_render_report_scorecard_brief_overall_gauge_delta_classes(self) -> None:
        cases = [
            (90, 82, 8, "score-delta-up", "+8 pts vs baseline"),
            (72, 80, -8, "score-delta-down", "-8 pts vs baseline"),
            (80, 80, 0, "score-delta-neutral", "No Change"),
        ]
        for score, baseline_score, delta, css_class, text in cases:
            with self.subTest(css_class=css_class):
                scorecard = {
                    "schema_version": "bot_entity_scorecard.v1",
                    "artifact_id": f"sc-{css_class}",
                    "entity_type": "client_asn",
                    "entity": "64500",
                    "score": score,
                    "baseline_score": baseline_score,
                    "score_delta_points": delta,
                    "score_delta_basis": "Baseline score is recomputed from baseline-period point-in-time rule inputs; rules requiring pre-baseline delta inputs are excluded.",
                    "band": "low_review",
                    "domain_scores": {"security_evidence": 0},
                    "features": [],
                }
                output, _ = self.render_report.render(
                    scorecard,
                    self.render_args(report_type="scorecard_brief", format="html"),
                )

                self.assertIn('aria-label="Overall Score"', output)
                self.assertIn(f'class="{css_class}">{text}</div>', output)

    def test_render_report_scorecard_rule_labels_use_explicit_feature_condition_map(
        self,
    ) -> None:
        self.assertEqual(
            self.render_report.rule_label_parts("querystring_diversity_high"),
            ("Query String Diversity", "High"),
        )
        self.assertEqual(
            self.render_report.rule_label_parts(
                "querystring_diversity_with_high_miss_rate"
            ),
            ("Query String Diversity", "With High Miss Rate"),
        )
        self.assertEqual(
            self.render_report.rule_label_parts("unknown_signal_shape"),
            ("Unknown Signal Shape", ""),
        )

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
        self.assertIn("sorted by lower health score", output)
        self.assertNotIn("Rank 1:", output)
        chart_output = output[output.index("Scorecard Ranking Bars") :]
        self.assertLess(
            chart_output.index("low.example.com"),
            chart_output.index("high.example.com"),
        )

    def test_render_report_html_mover_chart_present(self) -> None:
        shared_compat = {
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "comparison_type": "previous_window",
            "table_used": "bi_summary_hour",
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
                        },
                    ],
                },
            ],
        }
        output, _ = self.render_report.render(wrapper, self.render_args(format="html"))
        self.assertIn("Mover Contribution Bars", output)
        self.assertIn("total delta 500", output)
        chart_output = output[output.index("Mover Contribution Bars") :]
        self.assertLess(chart_output.index("64501"), chart_output.index("64500"))

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
        self.assertIn("sorted by lower health score", output)
        self.assertIn("/api/search", output)

    def test_render_report_html_edge_ops_optional_charts_present(self) -> None:
        shared_compat = {
            "scope": {"request_host": "www.example.com"},
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_windows": [{"start": "2026-03-25", "end": "2026-04-01"}],
            "comparison_type": "previous_window",
            "table_used": "bi_summary_hour",
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
        html, _ = self.render_report.render(wrapper, self.render_args(format="html"))
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

    def test_all_examples_render_html(self) -> None:
        example_dir = ROOT / "skills/bot-insights/examples"
        for path in sorted(example_dir.glob("*.json")):
            with self.subTest(example=path.name):
                wrapper = json.loads(path.read_text(encoding="utf-8"))
                html_output, _warnings = self.render_report.render(
                    wrapper, self.render_args(format="html")
                )
                self.assertIn("<!doctype html>", html_output)
                self.assertIn("<main>", html_output)
                self.assertIn("<h1>", html_output)


if __name__ == "__main__":
    unittest.main()
