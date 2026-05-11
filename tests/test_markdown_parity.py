"""Durable Markdown parity gate (M3.2).

For every wrapper-mode fixture under ``tests/fixtures/report_engine/``
and ``skills/bot-insights/examples/`` (mirroring the HTML parity
gate's discovery surface), render the wrapper twice — once through
the legacy ``render_report.py --format markdown`` path and once through
the engine ``report_engine/render.py --format markdown`` path — and
assert a small set of *data preservation* invariants. The two renderers
produce structurally different Markdown by design (the engine is a
redesign, not a port), so this gate is **not** a byte-equality or
section-order check; it verifies the engine carries the same
artifact-derived facts the legacy renderer surfaced.

The gate survives M3.2 → M3.3 (forcing routing) → M4.5 (legacy
deletion), at which point its semantic invariants migrate to
``tests/test_report_semantics.py`` as engine-only regression tests
alongside the HTML invariants.

Invariants enforced by this gate (the **data preservation** set):

1. **Exit-code parity** — legacy and engine agree on whether the wrapper
   is renderable (both succeed, or both reject with similar error
   messages).
2. **Both paths produce non-trivial Markdown** when both succeed.
3. **Posture metric names appear in both renders** — every metric the
   underlying posture artifact carries shows up as its raw identifier or
   its human-readable label in both outputs.

Invariants deliberately **NOT** in this gate (mirror the HTML gate's
pragmatic exclusions — they surface real divergences that belong in
their own milestone, and landing them here would make the gate noisy
without supporting engine fixes):

- **Byte envelope** — the engine is a redesign (per-card sections,
  triage-strip tables, narrative slots); engine Markdown is routinely
  ~2× legacy bytes. The engine's per-template smoke tests already gate
  byte regressions within the engine path.
- **Heading-set + section-order parity** — engine and legacy organize
  sections differently. The engine-only invariants in M4.5's
  ``tests/test_report_semantics.py`` will pin those once legacy is
  gone.
- **Warning-line parity** — the engine doesn't emit every legacy
  ``ctx.warn`` line yet. M3.3 (forcing engine markdown routing) picks
  up the missing emissions.

Fixture classification:

* **Wrapper** (``schema_version: bot_report_input.v1``): parity asserted.
* **Raw artifact**: filtered out at discovery (no wrapper schema).
* **Examples**: included if wrapper-shape; otherwise skipped.
* **Expected-failure**: legacy and engine both reject; asserted at the
  exit-code parity invariant.
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

RENDER_REPORT = ROOT / "skills/bot-insights/scripts/render_report.py"
ENGINE_RENDER = ROOT / "skills/bot-insights/scripts/report_engine/render.py"
FIXTURE_DIRS = (
    ROOT / "tests/fixtures/report_engine",
    ROOT / "skills/bot-insights/examples",
)

# render_report.py declares no uv inline deps; the engine path inside
# it needs jinja2 + markdown-it-py + bleach at import time. Pin the same
# extras the HTML parity gate uses so legacy and engine import surfaces
# are identical.
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


def _render_legacy(wrapper: Path) -> tuple[str, str, int]:
    """Render via legacy ``render_report.py --format markdown`` with
    ``BOT_INSIGHTS_RENDER_PATH=legacy``. The env var is harmless here
    (the markdown branch of ``render_report.render`` always uses
    legacy in M3.2 — the engine markdown wiring lands in M3.3), but
    pinning it makes the intent explicit and protects against future
    routing changes.
    """
    if not _have_uv():
        pytest.skip("uv not available")
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        out_path = Path(f.name)
    try:
        env = os.environ.copy()
        env["BOT_INSIGHTS_RENDER_PATH"] = "legacy"
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
                "markdown",
                "--output",
                str(out_path),
            ],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        md = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if result.returncode != 0:
            md = ""  # purge any leftover content from a previous successful run
        return md, result.stderr, result.returncode
    finally:
        out_path.unlink(missing_ok=True)


def _render_engine(wrapper: Path) -> tuple[str, str, int]:
    """Render via the engine's ``report_engine/render.py --format markdown``.

    The engine path is invoked directly because M3.2 does not yet wire
    markdown routing into ``render_report.py``'s ``BOT_INSIGHTS_RENDER_PATH``
    switch (that's M3.3). Calling ``render.py`` directly exercises the
    same code the M3.3 wiring will reach, so the gate is meaningful
    today and stable across that migration.
    """
    if not _have_uv():
        pytest.skip("uv not available")
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        out_path = Path(f.name)
    try:
        result = subprocess.run(
            [
                "uv",
                "run",
                "--quiet",
                str(ENGINE_RENDER),
                "--artifact",
                str(wrapper),
                "--out",
                str(out_path),
                "--format",
                "markdown",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        md = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if result.returncode != 0:
            md = ""
        return md, result.stderr, result.returncode
    finally:
        out_path.unlink(missing_ok=True)


def _render_both(wrapper: Path) -> tuple[tuple[str, str, int], tuple[str, str, int]]:
    """Shared helper: render via legacy and engine. Returns the
    ``(markdown, stderr, rc)`` tuple for each."""
    return _render_legacy(wrapper), _render_engine(wrapper)


def _posture_metric_names(wrapper: dict) -> list[str]:
    """Collect ``metrics[].name`` from any ``bot_posture_movement.v1``
    artifact in the wrapper. Anchors data-preservation assertions on
    the metric set every executive_posture / SOC / crawler / edge
    fixture carries.
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

_ERROR_RE = re.compile(r"^ERROR:\s*(.+?)\s*$", re.MULTILINE)


def _canonical_error(stderr: str) -> str | None:
    """First ``ERROR:`` line, stripped, or ``None`` if there is none."""
    match = _ERROR_RE.search(stderr)
    return match.group(1) if match else None


# Wrappers the legacy markdown dispatcher cannot render but the engine
# can — ``render_markdown`` in ``render_report.py`` has no branch for
# ``scorecard_entity_review``, so it falls through to a stub (header
# only). Listing the fixtures here makes the asymmetry explicit: the
# parity gate skips them with a documented reason rather than failing
# silently or, worse, "passing" because the stub trivially satisfies
# every invariant.
_LEGACY_UNSUPPORTED_REPORT_TYPES = frozenset({"scorecard_entity_review"})

# Wrappers that hit a strict-validation rejection in ``render_report.py``
# but render successfully via the engine. The legacy markdown branch
# flows through ``load_report_input``/``resolve_options``/
# ``validate_report_artifacts`` first, which rejects (e.g., index-only
# inputs missing a scorecard packet, or standalone scorecards without
# scope metadata). The engine's ``_resolve_module_from_wrapper`` is
# looser and renders these via degraded-mode templates instead.
#
# This asymmetry persists as long as ``_render_engine()`` invokes
# ``report_engine/render.py`` directly. M3.3 unifies the validation
# surface by routing engine markdown through ``render_report.py``
# (mirroring the HTML gate's ``BOT_INSIGHTS_RENDER_PATH=engine``
# pattern); when this gate's ``_render_engine()`` is updated to match
# at that point, the three fixtures here will become "expected
# failure on both paths" and the existing dual-rc-skip handles them
# without a named list. Until then, skipping is honest.
_VALIDATION_ASYMMETRIC_FIXTURES = frozenset({
    "crawler_governance_index_only.json",
    "edge_ops_impact_index_only.json",
    "scorecard_brief_acme_malicious_notes.json",
})


def _wrapper_report_type(wrapper: Path) -> str | None:
    try:
        data = json.loads(wrapper.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("report_type")


def _skip_if_legacy_unsupported(wrapper: Path) -> None:
    rt = _wrapper_report_type(wrapper)
    if rt in _LEGACY_UNSUPPORTED_REPORT_TYPES:
        pytest.skip(
            f"{wrapper.name} carries report_type {rt!r} which legacy "
            f"render_markdown() has no branch for; engine renders it "
            f"fully. Parity is asymmetric by design until M4 deletes "
            f"the legacy path."
        )
    if wrapper.name in _VALIDATION_ASYMMETRIC_FIXTURES:
        pytest.skip(
            f"{wrapper.name} is rejected by render_report.py validation "
            f"on the legacy markdown path but renders via the engine "
            f"(degraded mode). M3.3 unifies validation by routing engine "
            f"markdown through render_report.py."
        )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_md_parity_exit_codes_match(wrapper: Path):
    """The critical parity invariant: legacy and engine must agree on
    whether the wrapper is renderable. If one path raises and the other
    succeeds, that is a behavior divergence — either a fixture flaw
    surfacing only on one path, or a validation difference that needs
    addressing before M3.3 forces engine routing.
    """
    _skip_if_legacy_unsupported(wrapper)
    (_, legacy_err, legacy_rc), (_, engine_err, engine_rc) = _render_both(wrapper)
    assert (legacy_rc == 0) == (engine_rc == 0), (
        f"exit-code parity broken for {wrapper.name}: "
        f"legacy rc={legacy_rc}, engine rc={engine_rc}\n"
        f"legacy stderr tail: {legacy_err.splitlines()[-3:] if legacy_err else '<empty>'}\n"
        f"engine stderr tail: {engine_err.splitlines()[-3:] if engine_err else '<empty>'}"
    )
    if legacy_rc != 0:
        # Both failed — parity of failure. The ERROR lines should
        # agree on the *reason*. Allow minor wording drift via a
        # 30-char shared prefix, matching the HTML parity gate.
        legacy_error = _canonical_error(legacy_err)
        engine_error = _canonical_error(engine_err)
        assert legacy_error is not None and engine_error is not None, (
            f"missing canonical ERROR for {wrapper.name}: "
            f"legacy={legacy_error!r}, engine={engine_error!r}"
        )
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
def test_md_parity_both_paths_render_non_empty(wrapper: Path):
    """Sanity invariant: when both paths succeed, both emit
    non-trivial Markdown. ``render_markdown`` produces only the header
    + metadata for any report_type the dispatcher does not handle; the
    skip list above filters out the known case, so a sub-512-byte
    legacy render here would be a regression worth investigating.
    """
    _skip_if_legacy_unsupported(wrapper)
    (legacy_md, _, legacy_rc), (engine_md, _, engine_rc) = _render_both(wrapper)
    if legacy_rc != 0 and engine_rc != 0:
        pytest.skip(f"{wrapper.name} is an expected-failure fixture for both paths")
    assert legacy_rc == 0
    assert engine_rc == 0
    assert len(legacy_md) >= 512, (
        f"legacy markdown render too small for {wrapper.name}: "
        f"{len(legacy_md)} bytes"
    )
    assert len(engine_md) >= 512, (
        f"engine markdown render too small for {wrapper.name}: "
        f"{len(engine_md)} bytes"
    )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_md_parity_both_paths_emit_structural_markers(wrapper: Path):
    """On dual success, both outputs must carry the basic structural
    fingerprint of a rendered report: at least one H1 line and the
    ``Report type:`` metadata line. Cheap regression catcher for the
    header-only-stub failure mode (which would still hit the >= 512
    byte gate via padding) and for template regressions that drop the
    type fence.

    Deliberately does **not** assert shared section headers
    (``## Method``, ``## Executive Summary``, etc.) because the two
    renderers organize sections differently by design — legacy
    markdown emits ``## Lens Rollup`` / ``## Evidence Limits`` /
    ``## Top Scorecard Ranking`` while the engine emits ``## Method``
    / ``## Triage`` / ``## Coverage``. Section-level parity is an
    engine-only invariant that lives in M4.5's
    ``tests/test_report_semantics.py`` once legacy is gone.
    """
    _skip_if_legacy_unsupported(wrapper)
    (legacy_md, _, legacy_rc), (engine_md, _, engine_rc) = _render_both(wrapper)
    if legacy_rc != 0 or engine_rc != 0:
        pytest.skip(
            f"{wrapper.name} did not render on one or both paths; "
            f"structural markers asserted only on dual success"
        )
    for name, md in (("legacy", legacy_md), ("engine", engine_md)):
        # An H1 line — some legacy outputs prefix metadata before the
        # H1 while engine outputs lead with it, so any-line match.
        assert any(line.startswith("# ") for line in md.splitlines()), (
            f"{name} markdown render of {wrapper.name} carries no H1 line"
        )
        assert "Report type:" in md, (
            f"{name} markdown render of {wrapper.name} omits the "
            f"'Report type:' metadata line"
        )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_md_parity_posture_metric_names_appear_in_both_renders(wrapper: Path):
    """Every metric the posture artifact carries should appear in both
    renders, either as its raw identifier or its human-readable label.

    This is the v1 data-preservation invariant — keyed numeric
    assertions (every metric appears alongside its pct_change) come
    in a follow-up commit once the corpus is in steady state. Skipped
    for wrappers without a posture artifact (e.g., a standalone
    scorecard_brief without posture companion).
    """
    _skip_if_legacy_unsupported(wrapper)
    data = json.loads(wrapper.read_text())
    raw_names = _posture_metric_names(data)
    if not raw_names:
        pytest.skip(f"{wrapper.name} carries no posture metrics to anchor on")

    (legacy_md, _, legacy_rc), (engine_md, _, engine_rc) = _render_both(wrapper)
    if legacy_rc != 0 or engine_rc != 0:
        pytest.skip(
            f"{wrapper.name} did not render on one or both paths; "
            f"metric-presence asserted only on dual success"
        )

    # Allow either the raw snake_case identifier or its
    # METRIC_LABELS entry (e.g., "Total requests" for "requests").
    # md_escape backslash-escapes underscores, so we strip them from
    # both the haystack and the needle when comparing the raw form.
    sys.path.insert(0, str(ROOT / "skills/bot-insights/scripts"))
    from report_engine.humanize import METRIC_LABELS

    def _normalize(s: str) -> str:
        # Strip backslashes so md_escape's backslash-prefix form of
        # underscores, periods, etc. compares equal to the bare
        # identifier. Apply to both sides uniformly so a label that
        # happens to carry escaped punctuation (future METRIC_LABELS
        # entries) doesn't behave differently from the raw form.
        return s.replace("\\", "")

    def _contains(text: str, label: str) -> bool:
        return _normalize(label) in _normalize(text)

    for name in raw_names:
        labels = {name, METRIC_LABELS.get(name, name)}
        assert any(_contains(legacy_md, lbl) for lbl in labels), (
            f"legacy markdown render of {wrapper.name} omits metric "
            f"{name!r} (checked: {labels})"
        )
        assert any(_contains(engine_md, lbl) for lbl in labels), (
            f"engine markdown render of {wrapper.name} omits metric "
            f"{name!r} (checked: {labels})"
        )
