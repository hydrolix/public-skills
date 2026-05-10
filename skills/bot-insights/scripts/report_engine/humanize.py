"""Deterministic snake_case → human-readable label conversion.

The contract: anything appearing as a *label* in the report (band names,
confidence levels, comparison types, constraint phrases, cluster names,
column headers) must be human-readable and properly capitalized.

Variable references (rule names like `cache_miss_rate_high`, schema
identifiers like `bot_scorecard_artifacts.v1`) are exempt — those are
specific identifiers and may appear as-is, but only inside evidence/method
sections at the bottom of the report (progressive disclosure: high-level
plain-English at the top, technical identifiers at the bottom).
"""

from __future__ import annotations

# ---- Per-domain explicit mappings -----------------------------------------
# Use these where the auto-generated Title Case would be wrong or jargon-y.

BAND_LABELS = {
    "observe": "Observe",
    "monitor": "Monitor",
    "escalate": "Escalate",
}

CONFIDENCE_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
}

AUTHOR_TYPE_LABELS = {
    "llm": "AI assistant",
    "human": "Analyst",
    "analyst": "Analyst",
}

CONFIDENCE_REASON_LABELS = {
    "summary_table_used": "Summary table used",
    "retained_dimensions_fit": "Dimensions fit retained schema",
    "current_count_sufficient": "Current window has enough rows",
    "baseline_count_sufficient": "Baseline window has enough rows",
    "current_count_low": "Current window has few rows",
    "baseline_count_low": "Baseline window has few rows",
    "siem_unavailable": "SIEM data unavailable",
    "siem_available": "SIEM data available",
    "feature_input_missing": "Some feature inputs missing",
    "feature_input_complete": "All feature inputs present",
}

COMPARISON_TYPE_LABELS = {
    "previous_window": "Previous window",
    "before_window": "Before window",
    "explicit_target": "Explicit target",
    "external_model": "External model",
    "rolling_baseline": "Rolling baseline",
}

INTERPRETATION_CONSTRAINT_LABELS = {
    "rule_based_scorecard": "Rule-based scorecard",
    "mechanical_features_only": "Mechanical features only",
    "no_causal_claim": "No causal claim",
    "llm_may_summarize_structured_evidence_only": "LLM may summarize structured evidence only",
    "summary_first": "Summary-first analysis",
    "no_query_during_interpretation": "No Hydrolix queries during interpretation",
}

RULE_STATUS_LABELS = {
    "triggered": "Triggered",
    "evaluated_zero": "Below threshold",
    "missing_input": "Inputs missing",
    "not_evaluated": "Not evaluated",
}

# Display labels for the entity_type axis on per-entity scorecards. The SOC
# triage queue heading reads "ASN risk queue" / "host risk queue" / "IP risk
# queue" depending on what the producer ranked. Unknown identifiers fall
# back to humanize_identifier.
ENTITY_TYPE_LABELS = {
    "client_asn": "ASN",
    "request_host": "host",
    "client_ip": "IP",
    "user_agent": "user agent",
    "country": "country",
    "bot_class": "bot class",
    "ai_category": "AI category",
}

# Override map for known cluster/database names where the simple Title Case
# would be wrong. Extend as new tenants get onboarded.
CLUSTER_DISPLAY_OVERRIDES = {
    "acme": "Acme",
    "akamai": "Akamai",
    "trafficpeak": "TrafficPeak",
}


# ---- Generic helpers -------------------------------------------------------


def humanize_identifier(s: str) -> str:
    """Generic snake_case → 'Snake case' fallback for unmapped identifiers."""
    if not s:
        return ""
    return s.replace("_", " ").strip().capitalize()


def cluster_display(name: str) -> str:
    """Human-readable cluster/tenant name. Uses overrides, falls back to
    Title Case for snake_case, otherwise leaves as-is."""
    if not name:
        return ""
    if name in CLUSTER_DISPLAY_OVERRIDES:
        return CLUSTER_DISPLAY_OVERRIDES[name]
    if "_" in name or "-" in name:
        return name.replace("_", " ").replace("-", " ").title()
    # Single-token names like "acme" — Title Case the first letter
    # only; we can't deterministically split without a dictionary.
    return name[:1].upper() + name[1:]


# ---- Mapping wrappers ------------------------------------------------------


def humanize_band(s: str) -> str:
    return BAND_LABELS.get(s, humanize_identifier(s))


def humanize_confidence(s: str) -> str:
    return CONFIDENCE_LABELS.get(s, humanize_identifier(s))


def humanize_author_type(s: str) -> str:
    return AUTHOR_TYPE_LABELS.get(s, humanize_identifier(s))


def humanize_confidence_reason(s: str) -> str:
    return CONFIDENCE_REASON_LABELS.get(s, humanize_identifier(s))


def humanize_comparison_type(s: str) -> str:
    return COMPARISON_TYPE_LABELS.get(s, humanize_identifier(s))


def humanize_constraint(s: str) -> str:
    return INTERPRETATION_CONSTRAINT_LABELS.get(s, humanize_identifier(s))


def humanize_status(s: str) -> str:
    return RULE_STATUS_LABELS.get(s, humanize_identifier(s))


def humanize_entity_type(s: str) -> str:
    """Reader-facing noun for an entity_type identifier (e.g. ``client_asn``).

    Used in the SOC triage H1 ("…ASN risk queue") and as the unit label on
    per-entity tables and pills. Unknown entity types fall back to
    humanize_identifier so a new producer dimension still renders sensibly.
    """
    return ENTITY_TYPE_LABELS.get(s, humanize_identifier(s))
