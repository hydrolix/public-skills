"""Engine-only semantic invariants (M4.5 additive).

Carry forward the semantic content checks that the parity gates
verified across two renderers — but assert them against the engine
output alone. The parity gates retire alongside this suite (the
engine is the only renderer post-M4 for wrapper inputs); the
invariants those gates protected are still meaningful as a
regression check on the engine path itself.

Invariants enforced:

1. **Heading set is non-trivial** — at least one H1 + one H2 in
   every successful render. Catches a hero/content block silently
   collapsing.
2. **Posture metric names present** — every metric the underlying
   ``bot_posture_movement.v1`` artifact carries shows up in the
   rendered text (raw identifier or its ``METRIC_LABELS`` entry).
   The same anchor the parity gates used.
3. **Byte floor per format** — HTML >= 1024 bytes, Markdown >= 512
   bytes on success. Catches degenerate "header-only stub" renders.
4. **Analyst notes that landed in narrative slots appear in
   output** — when a wrapper carries an analyst_note whose
   ``note_id`` routes into a known slot, that note's text (or its
   bleach-safe HTML transform) must surface in the render. Catches
   slot-routing regressions.

The HTML and Markdown invariants share the same fixture corpus and
metric anchor; assertions branch by format. Uses the existing
``tests/_html_tree.py`` helper to parse HTML; Markdown assertions
are line/regex-level.
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
sys.path.insert(0, str(ROOT / "skills/bot-insights/scripts"))

import _html_tree as html_tree  # noqa: E402

RENDER_REPORT = ROOT / "skills/bot-insights/scripts/render_report.py"
FIXTURE_DIRS = (
    ROOT / "tests/fixtures/report_engine",
    ROOT / "skills/bot-insights/examples",
)
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


def _render(wrapper: Path, fmt: str) -> tuple[str, str, int]:
    if shutil.which("uv") is None:
        pytest.skip("uv not available")
    suffix = ".html" if fmt == "html" else ".md"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        out_path = Path(f.name)
    try:
        env = os.environ.copy()
        # Force engine routing — the suite asserts engine semantics.
        env["BOT_INSIGHTS_RENDER_PATH"] = "engine"
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
                fmt,
                "--output",
                str(out_path),
            ],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        body = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        return body, result.stderr, result.returncode
    finally:
        out_path.unlink(missing_ok=True)


def _posture_metric_names(wrapper: dict) -> list[str]:
    names: list[str] = []
    for artifact in wrapper.get("artifacts", []) or []:
        if artifact.get("schema_version") != "bot_posture_movement.v1":
            continue
        for metric in artifact.get("metrics", []) or []:
            name = metric.get("name")
            if name:
                names.append(str(name))
    return names


# Slots that carry **narrative prose** the renderer must surface in
# the output. Other slot keys (e.g. ``finding_overrides``) deliver
# transformation input that re-shapes ``ctx["findings"]`` rather than
# appearing verbatim — those slots must not be asserted for
# substring presence or the test would mis-fire on a future
# ``llm-finding-overrides`` fixture.
_NARRATIVE_SLOTS = frozenset({"executive_summary", "operational_interpretation"})


def _slot_routed_notes(wrapper: dict) -> list[tuple[str, str]]:
    """Returns ``[(slot_name, note_text), ...]`` for any analyst_note
    in the wrapper whose ``note_id`` maps to a **narrative** slot in
    the target report module's ``NOTE_ID_TO_SLOT`` registry.
    Non-narrative slots (e.g. ``finding_overrides``) are filtered
    because their text is transformation input, not rendered prose.

    Empty when the wrapper carries no qualifying notes or its
    report_type isn't in the engine registry — the test that
    consumes this list pytest.skips in those cases rather than
    producing a false "pass" by skipping every assertion.
    """
    from report_engine.contexts import REPORT_TYPE_REGISTRY

    rt = wrapper.get("report_type")
    module = REPORT_TYPE_REGISTRY.get(rt)
    if module is None:
        return []
    slot_map = getattr(module, "NOTE_ID_TO_SLOT", {}) or {}
    out: list[tuple[str, str]] = []
    for note in wrapper.get("analyst_notes", []) or []:
        slot = slot_map.get(note.get("note_id", ""))
        text = note.get("text") or ""
        if slot in _NARRATIVE_SLOTS and text.strip():
            out.append((slot, text))
    return out


# Wrappers that the legacy front-end (``load_report_input`` /
# ``resolve_options`` / ``validate_report_artifacts``) rejects with a
# ``ReportError`` before reaching the engine renderer. Asserting
# hard-pass on these would mask the validation behavior; asserting
# silent skip would mask a *new* fixture regression. We assert both
# the rejection rc *and* a fragment of the canonical error message so
# the test fails closed if either the validation surface or the
# fixture changes shape.
_EXPECTED_RENDER_FAILURES: dict[str, str] = {
    "crawler_governance_index_only.json": (
        "crawler_governance requires bot_entity_scorecard.v1"
    ),
    "edge_ops_impact_index_only.json": (
        "edge_ops_impact requires bot_entity_scorecard.v1"
    ),
    "scorecard_brief_acme_malicious_notes.json": (
        "scope metadata"
    ),
}


_WRAPPERS = _wrapper_fixtures()


# ---------------------------------------------------------------------------
# Renderability — carries forward the parity gate's exit-code invariant.
# ---------------------------------------------------------------------------


def _assert_rc(wrapper: Path, fmt: str, stderr: str, rc: int) -> None:
    """Renderability invariant: a wrapper either renders (rc == 0) or it
    is in the documented expected-failure set with a matching error
    message. Anything else fails the test closed — silently skipping
    a render failure would let a regression hide where the parity
    gates used to catch it.
    """
    expected_fragment = _EXPECTED_RENDER_FAILURES.get(wrapper.name)
    if expected_fragment is None:
        assert rc == 0, (
            f"{wrapper.name} {fmt} render failed (rc={rc}) and is not "
            f"in the expected-failure list; stderr tail: "
            f"{stderr.splitlines()[-3:] if stderr else '<empty>'}"
        )
        return
    assert rc != 0, (
        f"{wrapper.name} {fmt} render unexpectedly succeeded — "
        f"it is in _EXPECTED_RENDER_FAILURES with fragment "
        f"{expected_fragment!r}; remove it from the list or fix "
        f"the fixture"
    )
    assert expected_fragment in stderr, (
        f"{wrapper.name} {fmt} render failed but stderr does not "
        f"contain expected fragment {expected_fragment!r}; "
        f"stderr tail: {stderr.splitlines()[-3:]}"
    )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_html_renderability(wrapper: Path):
    _, stderr, rc = _render(wrapper, "html")
    _assert_rc(wrapper, "html", stderr, rc)


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_markdown_renderability(wrapper: Path):
    _, stderr, rc = _render(wrapper, "markdown")
    _assert_rc(wrapper, "markdown", stderr, rc)


# ---------------------------------------------------------------------------
# HTML invariants
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_html_heading_set_is_non_trivial(wrapper: Path):
    """A report-shaped HTML document must carry at least one H1 plus
    one H2. A render that drops below that has collapsed its hero
    block or its content block — almost always a template regression.
    """
    html, _, rc = _render(wrapper, "html")
    if rc != 0:
        pytest.skip(f"{wrapper.name} did not render to HTML")
    root = html_tree.parse(html)
    headings = html_tree.heading_sequence(root)
    levels = [tag for tag, _ in headings]
    assert "h1" in levels, (
        f"{wrapper.name} HTML render carries no H1 heading: "
        f"{levels[:8]}"
    )
    assert "h2" in levels, (
        f"{wrapper.name} HTML render carries no H2 heading: "
        f"{levels[:8]}"
    )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_html_posture_metric_names_present(wrapper: Path):
    """Every metric the posture artifact carries must appear in the
    rendered HTML, either as its raw identifier or its
    METRIC_LABELS entry. Inherited from the parity gates; engine
    output must keep this contract.
    """
    data = json.loads(wrapper.read_text())
    raw_names = _posture_metric_names(data)
    if not raw_names:
        pytest.skip(f"{wrapper.name} carries no posture metrics")
    html, _, rc = _render(wrapper, "html")
    if rc != 0:
        pytest.skip(f"{wrapper.name} did not render to HTML")
    from report_engine.humanize import METRIC_LABELS

    text = html_tree.parse(html).text()
    for name in raw_names:
        labels = {name, METRIC_LABELS.get(name, name)}
        assert any(lbl in text for lbl in labels), (
            f"engine HTML render of {wrapper.name} omits metric "
            f"{name!r} (checked: {labels})"
        )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_html_byte_floor(wrapper: Path):
    """Engine HTML on success is non-trivial. Header-only stubs sit
    well under 1 KB; full reports are well over. The floor catches
    the degenerate fallthrough mode silently.
    """
    html, _, rc = _render(wrapper, "html")
    if rc != 0:
        pytest.skip(f"{wrapper.name} did not render to HTML")
    assert len(html) >= 1024, (
        f"{wrapper.name} engine HTML render is too small: "
        f"{len(html)} bytes"
    )


# ---------------------------------------------------------------------------
# Markdown invariants
# ---------------------------------------------------------------------------

_MD_H1_RE = re.compile(r"^# ", re.MULTILINE)
_MD_H2_RE = re.compile(r"^## ", re.MULTILINE)


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_md_heading_set_is_non_trivial(wrapper: Path):
    md, _, rc = _render(wrapper, "markdown")
    if rc != 0:
        pytest.skip(f"{wrapper.name} did not render to Markdown")
    assert _MD_H1_RE.search(md), (
        f"{wrapper.name} markdown render carries no H1 line"
    )
    assert _MD_H2_RE.search(md), (
        f"{wrapper.name} markdown render carries no H2 line"
    )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_md_posture_metric_names_present(wrapper: Path):
    data = json.loads(wrapper.read_text())
    raw_names = _posture_metric_names(data)
    if not raw_names:
        pytest.skip(f"{wrapper.name} carries no posture metrics")
    md, _, rc = _render(wrapper, "markdown")
    if rc != 0:
        pytest.skip(f"{wrapper.name} did not render to Markdown")
    from report_engine.humanize import METRIC_LABELS

    # md_escape backslash-escapes underscores, dots, etc. — normalize
    # both sides so the substring search works against the bare
    # identifier.
    def _normalize(s: str) -> str:
        return s.replace("\\", "")

    norm_md = _normalize(md)
    for name in raw_names:
        labels = {name, METRIC_LABELS.get(name, name)}
        assert any(_normalize(lbl) in norm_md for lbl in labels), (
            f"engine markdown render of {wrapper.name} omits "
            f"metric {name!r} (checked: {labels})"
        )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_md_byte_floor(wrapper: Path):
    md, _, rc = _render(wrapper, "markdown")
    if rc != 0:
        pytest.skip(f"{wrapper.name} did not render to Markdown")
    assert len(md) >= 512, (
        f"{wrapper.name} engine markdown render is too small: "
        f"{len(md)} bytes"
    )


# ---------------------------------------------------------------------------
# Slot-routed analyst note placement
# ---------------------------------------------------------------------------


def _note_snippet(note_text: str) -> str:
    """Pull a short distinctive substring from a note. First 40 chars
    is enough to disambiguate without forcing whole-note presence —
    ``markdown_render`` and ``md_escape`` transform the prose in
    different ways across HTML and Markdown outputs, so a full-text
    equality check would be fragile.
    """
    return note_text.strip()[:40]


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_html_slot_routed_notes_surface(wrapper: Path):
    """Analyst notes routed into a narrative slot must surface in the
    rendered HTML. Slot routing lives in ``module.NOTE_ID_TO_SLOT``
    and is exercised via ``engine_render._build_notes_by_slot``. A
    regression that breaks slot routing would silently drop
    analyst-supplied prose without error — the parity gates didn't
    catch this; the engine snapshot tests cover it only by virtue of
    including the note text verbatim, which is the wrong
    abstraction. This test makes the invariant explicit.
    """
    data = json.loads(wrapper.read_text())
    notes = _slot_routed_notes(data)
    if not notes:
        pytest.skip(f"{wrapper.name} carries no slot-routed analyst notes")
    html, _, rc = _render(wrapper, "html")
    if rc != 0:
        pytest.skip(f"{wrapper.name} did not render to HTML")
    text = html_tree.parse(html).text()
    for slot, note_text in notes:
        snippet = _note_snippet(note_text)
        if not snippet:
            continue
        assert snippet in text, (
            f"{wrapper.name} HTML render omits the analyst note "
            f"routed to slot {slot!r}: {snippet!r}"
        )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_md_slot_routed_notes_surface(wrapper: Path):
    """Markdown analog of ``test_html_slot_routed_notes_surface``.
    Without this, a Markdown template could drop the executive_summary
    or operational_interpretation note rendering and still satisfy
    the H1/H2 + metric + byte-floor invariants. md_escape
    backslash-escapes punctuation in the note's prose, so the
    substring comparison normalizes both sides by stripping
    backslashes.
    """
    data = json.loads(wrapper.read_text())
    notes = _slot_routed_notes(data)
    if not notes:
        pytest.skip(f"{wrapper.name} carries no slot-routed analyst notes")
    md, _, rc = _render(wrapper, "markdown")
    if rc != 0:
        pytest.skip(f"{wrapper.name} did not render to Markdown")

    def _normalize(s: str) -> str:
        return s.replace("\\", "")

    norm_md = _normalize(md)
    for slot, note_text in notes:
        snippet = _normalize(_note_snippet(note_text))
        if not snippet:
            continue
        assert snippet in norm_md, (
            f"{wrapper.name} Markdown render omits the analyst note "
            f"routed to slot {slot!r}: {snippet!r}"
        )
