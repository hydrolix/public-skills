"""Per-report-type context preparers.

Each module exposes:
  - SCHEMA: the raw artifact schema_version it handles
  - REPORT_TYPE: the wrapper `report_type` it handles
  - TEMPLATE: the relative template path under templates/reports/
  - NOTE_ID_TO_SLOT: mapping from wrapper analyst_notes[].note_id to a
                     narrative slot name templates can render
  - assemble(artifacts: list[dict]) -> dict: reshape a wrapper's artifacts
                     list into the dict shape `prepare()` expects
  - prepare(artifact: dict) -> dict: pure transform from artifact to template
                     context
"""

from __future__ import annotations

from . import (
    control_review,
    crawler_governance,
    edge_ops_impact,
    executive_posture,
    scorecard_brief,
    scorecard_entity_review,
    soc_triage,
)

_MODULES = (
    scorecard_brief,
    scorecard_entity_review,
    executive_posture,
    control_review,
    soc_triage,
    crawler_governance,
    edge_ops_impact,
)

# Registry keyed on raw artifact schema_version. ``soc_triage``,
# ``crawler_governance``, and ``edge_ops_impact`` share
# ``bot_scorecard_artifacts.v1`` with ``scorecard_brief`` — the schema
# alone can't disambiguate them. We keep ``scorecard_brief`` as the
# schema-mode default; the others route through ``REPORT_TYPE_REGISTRY``
# via the wrapper's ``report_type`` field, the same path
# ``executive_posture`` uses.
_SCHEMA_REGISTRY_EXCLUSIONS = {"soc_triage", "crawler_governance", "edge_ops_impact"}
SCHEMA_REGISTRY = {
    mod.SCHEMA: mod
    for mod in _MODULES
    if mod.REPORT_TYPE not in _SCHEMA_REGISTRY_EXCLUSIONS
}

# Registry keyed on wrapper report_type
REPORT_TYPE_REGISTRY = {mod.REPORT_TYPE: mod for mod in _MODULES}

# Backward-compat alias for the original render.py callsite.
REGISTRY = SCHEMA_REGISTRY
