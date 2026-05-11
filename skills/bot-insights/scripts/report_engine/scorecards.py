"""Shared adapters for ``bot_entity_scorecard.v1`` cards.

Producers emit scorecards in two shapes:

- The current shape carries an explicit ``rule_results`` list, with each
  entry tagged ``triggered`` / ``evaluated_zero`` / ``missing_input``.
- Older artifacts (and the SOC-triage fixture in this skill's
  ``examples/``) instead emit ``features`` (triggered + below-threshold
  rolled into a flat list) and ``not_evaluated_features`` (the missing-
  input ones, with a ``reason`` field).

The engine's per-host code paths only need to deal with one shape, so
this module normalizes either form into the canonical ``rule_results``
shape and exposes the ``rule_counts`` projection both
``scorecard_brief`` and ``scorecard_entity_review`` already need.
"""

from __future__ import annotations


def normalize_rule_results(card: dict) -> list[dict]:
    """Return a canonical ``rule_results`` list for a scorecard card.

    Prefers the producer's explicit ``rule_results`` when present.
    Otherwise synthesizes one from ``features`` (status: ``triggered``)
    and ``not_evaluated_features`` (status: ``missing_input``). The
    synthesized list is the SOC fixture's path through the engine.
    """
    rule_results = card.get("rule_results")
    if isinstance(rule_results, list) and rule_results:
        return [r for r in rule_results if isinstance(r, dict)]

    synthesized: list[dict] = []
    for feature in card.get("features") or []:
        if not isinstance(feature, dict):
            continue
        result = dict(feature)
        result.setdefault("status", "triggered")
        synthesized.append(result)
    for feature in card.get("not_evaluated_features") or []:
        if not isinstance(feature, dict):
            continue
        result = dict(feature)
        result.setdefault("status", "missing_input")
        synthesized.append(result)
    return synthesized


def rule_counts(card: dict) -> dict:
    """Project a scorecard into ``{triggered, below_threshold,
    missing_input, total}``.

    The denominator (``total``) is the count of rules evaluated for the
    host. Both verdict classification and the confidence chip read from
    this dict, so centralizing it keeps the missing-input ratio
    consistent across reports.
    """
    rules = normalize_rule_results(card)
    triggered = sum(1 for r in rules if r.get("status") == "triggered")
    below = sum(1 for r in rules if r.get("status") == "evaluated_zero")
    missing = sum(1 for r in rules if r.get("status") == "missing_input")
    return {
        "triggered": triggered,
        "below_threshold": below,
        "missing_input": missing,
        "total": len(rules),
    }
