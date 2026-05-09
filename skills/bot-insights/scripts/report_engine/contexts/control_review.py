"""Stub context for `bot_control_review.v1` — port the existing renderer here."""

from __future__ import annotations

SCHEMA = "bot_control_review.v1"
REPORT_TYPE = "control_review"
TEMPLATE = "reports/control_review.html"

NOTE_ID_TO_SLOT = {
    "llm-interpretation": "executive_summary",
    "llm-operational": "operational_interpretation",
    "llm-finding-overrides": "finding_overrides",
}

PURPOSE = {
    "report_class_fleet": "Control Review — before/after for an applied control",
    "report_class_single": "Control Review — before/after for an applied control",
    "measures": (
        "Compares the after-control window against an explicit before window "
        "(or external baseline) for the entities targeted by the control."
    ),
    "score_legend": (
        "Per-metric direction and effect size, plus collateral and "
        "displacement checks for adjacent populations."
    ),
    "cant_say": (
        "Cannot claim the control caused the movement without external "
        "change evidence. Concurrent changes can confound the result."
    ),
}


def assemble(artifacts: list[dict]) -> dict:  # noqa: ARG001
    raise NotImplementedError(
        "control_review wrapper assembly not yet ported. "
        "Wrapper sends [primary, timeseries]; reshape into the prepare() input."
    )


def prepare(artifact: dict) -> dict:  # noqa: ARG001
    raise NotImplementedError(
        "control_review context not yet ported. "
        "Move logic from render_report.py into this module and create "
        "templates/reports/control_review.html."
    )
