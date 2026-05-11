#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "jinja2>=3.1",
#   "markdown-it-py>=3.0",
#   "bleach>=6.1",
# ]
# ///
"""Render a Bot Insights artifact or wrapper to self-contained HTML.

Accepts either a raw artifact (e.g. `bot_scorecard_artifacts.v1`) or a
`bot_report_input.v1` wrapper. Wrappers may carry `analyst_notes[]` whose
`note_id` values route into named narrative slots; deterministic content
fills the slots when notes are absent.

Usage:
  uv run report_engine/render.py --artifact path/to/artifact.json --out report.html
  uv run report_engine/render.py --artifact path/to/wrapper.json --out report.html
  uv run report_engine/render.py --input wrapper --artifact ... --out ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

# Allow running as a script *or* as a module
if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from report_engine import charts, findings as findings_mod, formatters
    from report_engine import humanize as humanize_mod
    from report_engine import markdown as md_mod
    from report_engine import theme
    from report_engine.contexts import REPORT_TYPE_REGISTRY, SCHEMA_REGISTRY
else:
    from . import charts, findings as findings_mod, formatters
    from . import humanize as humanize_mod
    from . import markdown as md_mod
    from . import theme
    from .contexts import REPORT_TYPE_REGISTRY, SCHEMA_REGISTRY


TEMPLATES_DIR = Path(__file__).parent / "templates"

WRAPPER_SCHEMA = "bot_report_input.v1"


def build_env(output_format: str = "html") -> Environment:
    """Build a Jinja2 environment for ``output_format`` rendering.

    HTML mode keeps the default autoescape policy (escape ``<``, ``>``,
    ``&``, etc. in interpolated values so producer-supplied text can't
    inject markup). Markdown mode disables autoescape — escaping
    HTML entities into a Markdown source document would render as
    literal ``&amp;`` in the final reading. Markdown templates are
    expected to apply the ``md_escape`` filter at every
    user/producer-controlled interpolation site instead.
    """
    if output_format == "markdown":
        autoescape = False  # md_escape filter is the escaping boundary
    else:
        autoescape = select_autoescape(["html"])
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=autoescape,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Charts return raw SVG — wrap in Markup so autoescape leaves them alone.
    env.globals["score_gauge"] = lambda *a, **kw: Markup(
        charts.score_gauge_svg(*a, **kw)
    )
    env.globals["score_bar"] = lambda *a, **kw: Markup(charts.score_bar_svg(*a, **kw))
    env.globals["coverage_bar"] = lambda *a, **kw: Markup(
        charts.coverage_bar_svg(*a, **kw)
    )
    env.globals["band_distribution_bar"] = lambda *a, **kw: Markup(
        charts.band_distribution_bar_svg(*a, **kw)
    )
    env.globals["score_histogram"] = lambda *a, **kw: Markup(
        charts.score_histogram_svg(*a, **kw)
    )
    env.globals["triage_histogram"] = lambda *a, **kw: Markup(
        charts.triage_histogram_svg(*a, **kw)
    )
    env.globals["sparkline"] = lambda *a, **kw: Markup(charts.sparkline_svg(*a, **kw))
    env.globals["bullet_chart"] = lambda *a, **kw: Markup(
        charts.bullet_chart_svg(*a, **kw)
    )
    env.globals["slopegraph"] = lambda *a, **kw: Markup(charts.slopegraph_svg(*a, **kw))
    env.globals["palette"] = theme.PALETTE
    # Markdown → safe HTML for analyst_notes prose
    env.globals["markdown_render"] = md_mod.render_safe
    # Formatters as filters
    env.filters["window_fmt"] = formatters.window_fmt
    env.filters["big_number"] = formatters.big_number
    env.filters["signed_pct"] = formatters.signed_pct
    env.filters["signed_pp"] = formatters.signed_pp
    env.filters["pct2"] = formatters.pct2
    env.filters["normalize_percents"] = formatters.normalize_percents
    # Humanization filters — apply to any snake_case identifier surfaced as a label.
    env.filters["humanize_band"] = humanize_mod.humanize_band
    env.filters["humanize_confidence"] = humanize_mod.humanize_confidence
    env.filters["humanize_author"] = humanize_mod.humanize_author_type
    env.filters["humanize_reason"] = humanize_mod.humanize_confidence_reason
    env.filters["humanize_comparison"] = humanize_mod.humanize_comparison_type
    env.filters["humanize_constraint"] = humanize_mod.humanize_constraint
    env.filters["humanize_status"] = humanize_mod.humanize_status
    env.filters["humanize"] = humanize_mod.humanize_identifier
    env.filters["cluster_display"] = humanize_mod.cluster_display
    # md_escape escapes Markdown-syntactic characters in producer-supplied
    # strings. Available in both HTML and Markdown envs (HTML templates
    # never need it, but registering it keeps the filter set consistent
    # so an accidental .md.j2 → .html template move doesn't break).
    env.filters["md_escape"] = md_mod.md_escape
    return env


def _build_notes_by_slot(
    notes: list[dict], note_id_to_slot: dict[str, str]
) -> dict[str, dict]:
    """Project wrapper analyst_notes into a slot-keyed dict via note_id."""
    out: dict[str, dict] = {}
    for note in notes or []:
        slot = note_id_to_slot.get(note.get("note_id", ""))
        if slot:
            # First-write wins; later notes with the same slot are ignored.
            out.setdefault(slot, note)
    return out


def _detect_input_kind(data: dict, override: str) -> str:
    if override != "auto":
        return override
    if data.get("schema_version") == WRAPPER_SCHEMA:
        return "wrapper"
    return "artifact"


def _maybe_promote_singleton(module, artifact: dict):
    """Promote a singleton scorecard_brief bundle to scorecard_entity_review.

    Returns (new_module, new_artifact) or None if no promotion applies.
    """
    if module.REPORT_TYPE != "scorecard_brief":
        return None
    if len(artifact.get("scorecards") or []) != 1:
        return None
    target = REPORT_TYPE_REGISTRY.get("scorecard_entity_review")
    if target is None:
        return None
    return target, target.assemble(artifact)


def _resolve_module_from_wrapper(data: dict):
    report_type = data.get("report_type")
    if report_type not in REPORT_TYPE_REGISTRY:
        raise SystemExit(
            f"No context preparer for report_type {report_type!r}. "
            f"Known: {sorted(REPORT_TYPE_REGISTRY)}"
        )
    return REPORT_TYPE_REGISTRY[report_type]


def _resolve_module_from_artifact(data: dict, schema_override: str | None):
    schema = schema_override or data.get("schema_version")
    if schema not in SCHEMA_REGISTRY:
        raise SystemExit(
            f"No context preparer for schema {schema!r}. "
            f"Known: {sorted(SCHEMA_REGISTRY)}"
        )
    return SCHEMA_REGISTRY[schema]


def template_for(module, output_format: str) -> str:
    """Pick the template path for ``module`` in ``output_format``.

    Each context module exposes ``TEMPLATE`` pointing at the HTML
    template (e.g. ``reports/executive_posture.html``). The Markdown
    sibling lives next to it with the ``.md.j2`` suffix. M3.1 selects
    by filename suffix per plan v3 (no separate registry needed).
    """
    if output_format == "markdown":
        # Replace the .html suffix with .md.j2. The TEMPLATE constant
        # always ends in .html across the existing context modules.
        if not module.TEMPLATE.endswith(".html"):
            raise ValueError(
                f"{module.REPORT_TYPE} TEMPLATE {module.TEMPLATE!r} does not "
                "end in .html; cannot derive a .md.j2 sibling."
            )
        return module.TEMPLATE[: -len(".html")] + ".md.j2"
    return module.TEMPLATE


def render(
    artifact_path: Path,
    out_path: Path,
    schema_override: str | None = None,
    input_kind: str = "auto",
    mode: str = "full",
    output_format: str = "html",
) -> None:
    """Render an artifact or wrapper to ``output_format``.

    ``output_format`` is ``"html"`` (default) or ``"markdown"``.
    Markdown mode renders the sibling ``.md.j2`` template via a
    Markdown-flavored Jinja2 env (autoescape off; ``md_escape``
    filter on). The context the templates consume is format-agnostic
    — ``module.prepare()`` is called once and the same dict feeds
    either renderer.
    """
    data = json.loads(artifact_path.read_text())
    kind = _detect_input_kind(data, input_kind)

    if kind == "wrapper":
        module = _resolve_module_from_wrapper(data)
        artifact = module.assemble(data["artifacts"])
        # Auto-promote a singleton scorecard_brief wrapper to the entity-review
        # report type so the renderer surfaces per-host evidence rather than
        # fleet aggregates collapsed to N=1. Producers don't need to know
        # about the new report_type.
        promoted = _maybe_promote_singleton(module, artifact)
        if promoted is not None:
            module, artifact = promoted
        notes_by_slot = _build_notes_by_slot(
            data.get("analyst_notes", []),
            getattr(module, "NOTE_ID_TO_SLOT", {}),
        )
    else:
        module = _resolve_module_from_artifact(data, schema_override)
        artifact = data
        notes_by_slot = {}

    ctx = module.prepare(artifact)
    ctx["notes_by_slot"] = notes_by_slot
    if hasattr(module, "post_prepare"):
        module.post_prepare(ctx)
    ctx["mode"] = mode

    # Apply per-finding LLM overrides if the wrapper carried any.
    overrides_note = notes_by_slot.get("finding_overrides")
    if overrides_note and "findings" in ctx:
        ctx["findings"] = findings_mod.apply_finding_overrides(
            ctx["findings"],
            overrides_note.get("text"),
        )

    env = build_env(output_format=output_format)
    template_path = template_for(module, output_format)
    template = env.get_template(template_path)
    out_path.write_text(template.render(**ctx))
    print(f"wrote {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--artifact",
        type=Path,
        required=True,
        help="Path to the artifact or wrapper JSON.",
    )
    ap.add_argument(
        "--out", type=Path, required=True, help="Path to write the HTML output."
    )
    ap.add_argument(
        "--schema",
        default=None,
        help="Override schema_version detection (raw artifact only).",
    )
    ap.add_argument(
        "--input",
        choices=["auto", "wrapper", "artifact"],
        default="auto",
        help="Force input shape; default auto-detects via schema_version.",
    )
    ap.add_argument(
        "--mode",
        choices=["brief", "full"],
        default="full",
        help="brief = exec one-pager; full = analyst exhibit (default).",
    )
    ap.add_argument(
        "--format",
        choices=["html", "markdown"],
        default="html",
        help="Output format. Markdown renders the sibling .md.j2 template.",
    )
    args = ap.parse_args()
    render(
        args.artifact, args.out, args.schema, args.input, args.mode, args.format
    )


if __name__ == "__main__":
    main()
