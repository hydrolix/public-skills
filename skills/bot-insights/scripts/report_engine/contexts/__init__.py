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
    executive_posture,
    scorecard_brief,
    scorecard_entity_review,
)

_MODULES = (
    scorecard_brief,
    scorecard_entity_review,
    executive_posture,
    control_review,
)

# Registry keyed on raw artifact schema_version
SCHEMA_REGISTRY = {mod.SCHEMA: mod for mod in _MODULES}

# Registry keyed on wrapper report_type
REPORT_TYPE_REGISTRY = {mod.REPORT_TYPE: mod for mod in _MODULES}

# Backward-compat alias for the original render.py callsite.
REGISTRY = SCHEMA_REGISTRY
