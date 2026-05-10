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

import json
from typing import Any

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

# Reader-facing labels for metric identifiers. Union of the legacy
# ``render_report.METRIC_LABELS`` and the ``executive_posture`` context's
# private copy. Producers that emit identifiers not in this map get a
# snake_case → "Snake case" fallback via ``human_metric_name``.
METRIC_LABELS = {
    "ai_requests": "AI requests",
    "bot_like_requests": "Bot-like requests",
    "bot_share_pct": "Bot share",
    "cache_misses": "Cache misses",
    "cache_miss_rate_pct": "Cache miss rate",
    "error_5xx_requests": "5xx errors",
    "rate_429_pct": "429 rate",
    "rate_limited_requests": "429 rate-limited requests",
    "requests": "Total requests",
    "avg_bot_score": "Average bot score",
    "siem_auth_fail_requests": "SIEM auth failures",
    "siem_blocked_requests": "SIEM blocked requests",
    "unique_client_ips": "Unique client IPs",
}

# Known feature/rule identifiers and their (axis, condition) label pair.
# Used by both renderers when surfacing trigger rationale in evidence rows.
# Unknown identifiers fall back to (display_label(text), "") via
# ``rule_label_parts``.
_RULE_LABEL_PARTS = {
    "new_entity": ("Entity", "New"),
    "volume_delta_high": ("Request Volume", "High Increase"),
    "contribution_to_total_delta_high": ("Contribution To Total", "High Delta"),
    "bot_share_delta_high": ("Bot Share", "High Increase"),
    "cache_miss_rate_high": ("Cache Miss Rate", "High"),
    "cache_miss_delta_high": ("Cache Miss Rate", "High Increase"),
    "origin_p95_delta_high": ("Origin P95", "High Increase"),
    "origin_cost_contribution_high": ("Origin Cost Contribution", "High"),
    "querystring_diversity_high": ("Query String Diversity", "High"),
    "querystring_diversity_with_high_miss_rate": (
        "Query String Diversity",
        "With High Miss Rate",
    ),
    "rate_429_delta_high": ("429 Rate", "High Increase"),
    "rate_5xx_delta_high": ("5xx Rate", "High Increase"),
    "good_bot_429_present": ("Good Bot 429 Responses", "Present"),
    "good_bot_error_rate_high": ("Good Bot Error Rate", "High"),
    "policy_surface_failure_present": ("Policy Surface Failures", "Present"),
    "ai_crawler_growth_high": ("AI Crawler Growth", "High"),
    "good_bot_policy_collateral_present": (
        "Good Bot Policy Collateral",
        "Present",
    ),
    "policy_collateral_error_rate_high": (
        "Policy Collateral Error Rate",
        "High",
    ),
    "displacement_delta_high": ("Displacement", "High Increase"),
    "siem_blocked_present": ("SIEM Blocked Requests", "Present"),
    "siem_auth_fail_present": ("SIEM Auth Failures", "Present"),
    "bad_bot_share_high": ("Bad Bot Share", "High"),
}

# Display-label tokens that need explicit Title Case overrides. The
# acronym set keeps trailing identifiers like ``api`` / ``url`` upper-cased
# instead of being title-cased to ``Api`` / ``Url``.
_DISPLAY_LABEL_ACRONYMS = {"ai", "api", "asn", "cdn", "ip", "seo", "siem", "url"}
_DISPLAY_LABEL_TOKEN_OVERRIDES = {"querystring": "Query String"}


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


def stringify(value: Any) -> str:
    """Stable text representation for any artifact value.

    Mirrors the legacy ``render_report.stringify`` so both renderers and
    every label helper agree on how to coerce arbitrary JSON values into
    display strings. ``None`` becomes ``"unavailable"``; bools become
    ``"true"`` / ``"false"``; lists/dicts JSON-encode.
    """
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ": "))


def display_label(value: Any) -> str:
    """Title-case a snake_case identifier with acronym preservation.

    Tokens in ``_DISPLAY_LABEL_ACRONYMS`` are upper-cased
    (``api`` → ``API``); tokens in ``_DISPLAY_LABEL_TOKEN_OVERRIDES`` get
    explicit replacements (``querystring`` → ``Query String``); leading
    digits keep their token unchanged; every other token is title-cased.
    """
    words: list[str] = []
    for token in stringify(value).replace("_", " ").split():
        lower = token.lower()
        if lower in _DISPLAY_LABEL_TOKEN_OVERRIDES:
            words.append(_DISPLAY_LABEL_TOKEN_OVERRIDES[lower])
        elif lower in _DISPLAY_LABEL_ACRONYMS:
            words.append(lower.upper())
        elif token and token[0].isdigit():
            words.append(token)
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return " ".join(words)


def human_metric_name(value: Any) -> str:
    """Reader-facing label for a metric identifier.

    Known identifiers (``METRIC_LABELS``) return their explicit label;
    unknown ones fall through unchanged so any escaping or sanitization
    applied downstream still sees the producer's exact identifier. This
    matches the legacy renderer's behavior (test coverage in
    ``test_render_report_markdown_escapes_user_controlled_metacharacters``
    depends on it).
    """
    text = stringify(value)
    return METRIC_LABELS.get(text, text)


def rule_label_parts(value: Any) -> tuple[str, str]:
    """Return ``(axis, condition)`` for a rule/feature identifier.

    Known identifiers (``_RULE_LABEL_PARTS``) return their explicit pair;
    unknown identifiers fall back to ``(display_label(text), "")``.
    """
    text = stringify(value)
    return _RULE_LABEL_PARTS.get(text, (display_label(text), ""))
