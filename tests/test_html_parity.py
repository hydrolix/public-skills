"""Durable HTML parity gate (M2.1 + M2.2).

For every wrapper-mode fixture, render the wrapper twice through
``render_report.py`` — once forced to the legacy ``html_*`` path and once
forced to the report_engine path — and assert a small set of *data
preservation* invariants. The two renderers produce structurally
different HTML by design (the engine is a redesign, not a port), so this
gate is **not** a byte-equality check; it verifies the engine carries the
same artifact-derived facts the legacy renderer surfaced.

The gate survives M2.1→M2.2→M3.x→M4.5 and gets retired with the legacy
deletion in M4.5, at which point its semantic invariants migrate to
``tests/test_report_semantics.py`` as engine-only regression tests.

Invariants enforced by this gate (the **data preservation** set):

1. **Exit-code parity** — legacy and engine agree on whether the wrapper
   is renderable (both succeed, or both reject with similar error
   messages).
2. **Both paths produce non-trivial HTML** when both succeed.
3. **Posture metric names appear in both renders** — every metric the
   underlying posture artifact carries shows up as its raw identifier or
   its human-readable label in both outputs.
4. **Allowlist file is well-formed.**

Invariants deliberately **NOT** in this gate (they surface real
divergences that belong in their own milestone — landing here would
make the gate noisy without the supporting engine fixes):

- **Byte envelope** — the engine is a redesign (gauges, narrative
  slots, score landscape); engine renders are routinely ~2× legacy
  bytes. Cross-renderer byte equality isn't a parity invariant for a
  redesign. The engine's own per-fixture snapshot tests already gate
  byte regressions within the engine path.
- **Warning-line parity** — the engine doesn't emit every legacy
  ``ctx.warn`` line yet (e.g., crawler_governance's "found N relevant
  missing feature inputs"). M2.3 picks up the missing warning
  emission as part of forcing engine routing for all 7 types.
- **Scope-label parity** — engine H1s use
  ``cluster_display(scope.cluster)`` rather than the wrapper's
  ``scope_label``, so wrappers whose ``scope_label`` differs from the
  cluster name don't include it. M2.3 threads ``scope_label`` into
  engine context preparers.
- **Heading-set + order, keyed numeric content** — these come back in
  M4.5's ``tests/test_report_semantics.py`` as engine-only regression
  tests, when the engine output is the only thing being checked.

Fixture classification:

* **Wrapper** (``schema_version: bot_report_input.v1``): parity asserted.
* **Raw artifact**: skipped with ``pytest.skip("raw-mode out of scope")``.
* **Examples**: included if wrapper-shape; otherwise skipped.
* **Expected-failure**: not present in the corpus today; would be
  asserted as "both paths raise the same ReportError" if added.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import _html_tree as html_tree  # noqa: E402

RENDER_REPORT = ROOT / "skills/bot-insights/scripts/render_report.py"
FIXTURE_DIRS = (
    ROOT / "tests/fixtures/report_engine",
    ROOT / "skills/bot-insights/examples",
)
ALLOWLIST_PATH = ROOT / "tests/fixtures/parity_allowlist.json"

# uv inline dependencies are declared on ``report_engine/render.py`` but
# not on ``render_report.py`` — for parity runs we install the same set
# at the call site so the engine path inside render_report.py can import
# jinja2 / bleach / markdown-it-py without a silent ImportError fallback.
_UV_WITH = (
    "--with",
    "jinja2",
    "--with",
    "markdown-it-py",
    "--with",
    "bleach",
)


def _wrapper_fixtures() -> list[Path]:
    found: list[Path] = []
    for d in FIXTURE_DIRS:
        if not d.exists():
            continue
        for path in sorted(d.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("schema_version") == "bot_report_input.v1":
                found.append(path)
    return found


def _ids(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


def _have_uv() -> bool:
    return shutil.which("uv") is not None


def _render_path(wrapper: Path, render_path: str) -> tuple[str, str, int]:
    """Render ``wrapper`` via ``render_report.py`` under uv with the
    ``BOT_INSIGHTS_RENDER_PATH`` override pinned to ``render_path``.

    Returns ``(html, stderr, returncode)``. Skips the test if ``uv`` is
    not available — the suite already gates the snapshot tests on this.
    """
    if not _have_uv():
        pytest.skip("uv not available")
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        out_path = Path(f.name)
    try:
        env = os.environ.copy()
        env["BOT_INSIGHTS_RENDER_PATH"] = render_path
        result = subprocess.run(
            [
                "uv",
                "run",
                "--quiet",
                *_UV_WITH,
                "python",
                str(RENDER_REPORT),
                "--file",
                str(wrapper),
                "--format",
                "html",
                "--output",
                str(out_path),
            ],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        html = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        return html, result.stderr, result.returncode
    finally:
        out_path.unlink(missing_ok=True)


def _posture_metric_names(wrapper: dict) -> list[str]:
    """Collect ``metrics[].name`` from any ``bot_posture_movement.v1``
    artifact in the wrapper. Other report types whose facts to assert on
    come later; for the first cut we anchor on posture's metric set
    because every executive_posture/SOC/crawler/edge fixture carries it.
    """
    names: list[str] = []
    for artifact in wrapper.get("artifacts", []) or []:
        if artifact.get("schema_version") != "bot_posture_movement.v1":
            continue
        for metric in artifact.get("metrics", []) or []:
            name = metric.get("name")
            if name:
                names.append(str(name))
    return names


_WRAPPERS = _wrapper_fixtures()


def _render_both(wrapper: Path) -> tuple[tuple[str, str, int], tuple[str, str, int]]:
    """Shared helper: render via legacy and engine. Returns the
    ``(html, stderr, rc)`` tuple for each. Caller checks parity."""
    legacy = _render_path(wrapper, "legacy")
    engine = _render_path(wrapper, "engine")
    return legacy, engine


_ERROR_RE = re.compile(r"^ERROR:\s*(.+?)\s*$", re.MULTILINE)


def _canonical_error(stderr: str) -> str | None:
    """First ``ERROR:`` line, stripped, or ``None`` if there is none."""
    match = _ERROR_RE.search(stderr)
    return match.group(1) if match else None


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_parity_exit_codes_match(wrapper: Path):
    """The critical parity invariant: legacy and engine must agree on
    whether the wrapper is renderable. If one path raises and the other
    succeeds, that is a behavior divergence — either a fixture flaw
    surfacing only on one path, or a validation difference that needs
    addressing before M2.3 forces engine routing."""
    (_, legacy_err, legacy_rc), (_, engine_err, engine_rc) = _render_both(wrapper)
    assert (legacy_rc == 0) == (engine_rc == 0), (
        f"exit-code parity broken for {wrapper.name}: "
        f"legacy rc={legacy_rc}, engine rc={engine_rc}\n"
        f"legacy stderr tail: {legacy_err.splitlines()[-3:] if legacy_err else '<empty>'}\n"
        f"engine stderr tail: {engine_err.splitlines()[-3:] if engine_err else '<empty>'}"
    )
    if legacy_rc != 0:
        # Both failed — parity of failure. The ERROR lines should agree
        # on the *reason* (the specific schema validation that
        # rejected the wrapper). Strict-match the first ERROR: line.
        legacy_error = _canonical_error(legacy_err)
        engine_error = _canonical_error(engine_err)
        assert legacy_error is not None and engine_error is not None
        # Allow minor wording drift: assert the rejection mentions the
        # same schema/field. Conservative substring overlap.
        # Take the longest shared 30-char prefix as the parity anchor.
        prefix_len = 0
        for i in range(min(len(legacy_error), len(engine_error))):
            if legacy_error[i] != engine_error[i]:
                break
            prefix_len = i + 1
        assert prefix_len >= 30 or legacy_error == engine_error, (
            f"failure reasons diverge for {wrapper.name}:\n"
            f"legacy: {legacy_error}\nengine: {engine_error}"
        )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_parity_both_paths_render_non_empty_html(wrapper: Path):
    """Sanity invariant: when both paths succeed, both emit non-trivial HTML."""
    (legacy_html, _, legacy_rc), (engine_html, _, engine_rc) = _render_both(wrapper)
    if legacy_rc != 0 and engine_rc != 0:
        pytest.skip(f"{wrapper.name} is an expected-failure fixture for both paths")
    assert legacy_rc == 0
    assert engine_rc == 0
    assert len(legacy_html) >= 1024, (
        f"legacy render too small for {wrapper.name}: {len(legacy_html)} bytes"
    )
    assert len(engine_html) >= 1024, (
        f"engine render too small for {wrapper.name}: {len(engine_html)} bytes"
    )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_parity_posture_metric_names_appear_in_both_renders(wrapper: Path):
    """Every metric the posture artifact carries should appear in both
    renders, either as its raw identifier or its human-readable label.

    This is the v1 data-preservation invariant — keyed numeric
    assertions (every metric appears alongside its pct_change) come in
    a follow-up commit once the corpus is in steady state. Skipped for
    wrappers without a posture artifact (e.g., scorecard_brief without
    posture companion).
    """
    data = json.loads(wrapper.read_text())
    raw_names = _posture_metric_names(data)
    if not raw_names:
        pytest.skip(f"{wrapper.name} carries no posture metrics to anchor on")

    # Allow either the raw snake_case identifier or its
    # METRIC_LABELS entry (e.g., "Total requests" for "requests").
    sys.path.insert(0, str(ROOT / "skills/bot-insights/scripts"))
    from report_engine.humanize import METRIC_LABELS

    legacy_html, _, _ = _render_path(wrapper, "legacy")
    engine_html, _, _ = _render_path(wrapper, "engine")
    legacy_tree = html_tree.parse(legacy_html)
    engine_tree = html_tree.parse(engine_html)
    legacy_text = legacy_tree.text()
    engine_text = engine_tree.text()

    for name in raw_names:
        labels = {name, METRIC_LABELS.get(name, name)}
        assert any(label in legacy_text for label in labels), (
            f"legacy render of {wrapper.name} omits metric {name!r} "
            f"(checked: {labels})"
        )
        assert any(label in engine_text for label in labels), (
            f"engine render of {wrapper.name} omits metric {name!r} "
            f"(checked: {labels})"
        )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
def test_parity_allowlist_is_well_formed():
    """The class-rename allowlist file must be valid JSON with the
    expected top-level keys, so M2.3 / M3.2 don't fail with cryptic
    errors when they read it."""
    data = json.loads(ALLOWLIST_PATH.read_text())
    assert "class_renames" in data
    assert isinstance(data["class_renames"], dict)
    # Each value must be a string (the new class name); _comment keys
    # and metadata lists are ignored by the parity engine.
    for old, new in data["class_renames"].items():
        assert isinstance(old, str)
        assert isinstance(new, str), f"allowlist class rename {old!r} value not a string"
