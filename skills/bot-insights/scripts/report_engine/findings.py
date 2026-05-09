"""Finding model and builders.

A `Finding` is one exec-quotable takeaway from the data. Templates render
`headline` (LLM-supplied override, optional) when present, else `title`.
`body` is supporting detail rendered below.

`finding_id` is a stable identifier so wrapper-supplied LLM overrides
(via the `llm-finding-overrides` slot) can target specific findings.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from . import scorecards as scorecards_mod
from .theme import DOMAIN_LABELS


@dataclass
class Finding:
    finding_id: str
    title: str
    body: str
    headline: str | None = None
    priority: int = 0
    # Optional structured recommendation. When present, templates render it
    # as a labeled "Recommended action" line below the body so the action
    # stands out visually rather than getting buried in prose.
    recommendation: str | None = None
    # Optional caveat — typically a coverage warning. Rendered as a smaller
    # muted line so it doesn't compete with the recommendation.
    caveat: str | None = None


def build_scorecard_brief_findings(
    scorecards: list[dict],
    n_total: int,
    n_with_triggers: int,
    n_clean: int,
    n_moved: int,
    domain_counts: Counter,
    coverage: dict[str, dict[str, int]],
) -> list[Finding]:
    findings: list[Finding] = []

    rule_fires: Counter = Counter()
    for sc in scorecards:
        for r in scorecards_mod.normalize_rule_results(sc):
            if r.get("status") == "triggered":
                rule_fires[r.get("name") or ""] += 1

    if rule_fires and n_with_triggers > 0:
        top_rule, top_n = rule_fires.most_common(1)[0]
        primary = next(
            (d for d, _ in domain_counts.most_common() if d != "none"),
            None,
        )
        domain_phrase = (
            DOMAIN_LABELS.get(primary, primary).lower() if primary else "rule"
        )

        if top_n >= 3 and top_n >= 0.8 * n_with_triggers:
            findings.append(
                Finding(
                    finding_id="shared_signal",
                    title=(
                        f"{top_n} hosts share one {domain_phrase} signal — "
                        f"investigate as one issue, not {top_n}"
                    ),
                    body=(
                        f"`{top_rule}` fired on {top_n} of {n_total} hosts; "
                        f"the other triggered rules are sparse. A shared signal "
                        f"more often points to a single fleet-wide cause than to "
                        f"{top_n} independent occurrences."
                    ),
                    priority=10,
                )
            )
        else:
            findings.append(
                Finding(
                    finding_id="multi_signal",
                    title=(
                        f"{n_with_triggers} of {n_total} hosts triggered at least one rule"
                    ),
                    body=(
                        f"Top rule: `{top_rule}` ({top_n} hosts). "
                        f"The signal mix is varied — investigate per-host before "
                        f"concluding a shared cause."
                    ),
                    priority=8,
                )
            )
    elif n_total > 0:
        findings.append(
            Finding(
                finding_id="all_clean",
                title=f"All {n_total} hosts produced no triggered rules",
                body=(
                    "No mechanical signals crossed thresholds in this window. "
                    "Treat as a baseline observation rather than a positive control."
                ),
                priority=10,
            )
        )

    total_missing = sum(c.get("missing_input", 0) for c in coverage.values())
    total_rules = sum(sum(c.values()) for c in coverage.values())
    if total_missing and total_rules:
        pct_missing = 100 * total_missing / total_rules
        pct_missing_str = f"{pct_missing:.2f}%"
        if pct_missing >= 50:
            findings.append(
                Finding(
                    finding_id="coverage_ceiling",
                    title=(
                        f"Confidence is bounded by feature coverage — "
                        f"{pct_missing_str} of rule evaluations had missing inputs"
                    ),
                    body=(
                        f"{total_missing} of {total_rules} rule evaluations could "
                        f"not be scored because their feature inputs were not "
                        f"available. Real risk may be higher than the score "
                        f"implies."
                    ),
                    priority=9,
                )
            )
        else:
            findings.append(
                Finding(
                    finding_id="coverage_partial",
                    title=(f"{pct_missing_str} of rule evaluations had missing inputs"),
                    body=(
                        f"{total_missing} of {total_rules} rule evaluations could "
                        f"not be scored. Coverage is partial but not severely limiting."
                    ),
                    priority=5,
                )
            )

    if n_moved == 0:
        findings.append(
            Finding(
                finding_id="no_movement",
                title="No host scores moved versus baseline",
                body=(
                    f"All {n_total} hosts have a score delta of 0 against the "
                    f"prior equivalent window. This report describes the current "
                    f"health snapshot, not change over time."
                ),
                priority=4,
            )
        )
    elif n_moved <= max(1, n_total // 4):
        findings.append(
            Finding(
                finding_id="few_moved",
                title=(
                    f"{n_moved} of {n_total} hosts moved versus baseline — "
                    f"movement is concentrated"
                ),
                body=(
                    "Most of the fleet is stable. Investigate the moved hosts in "
                    "isolation rather than as a broad shift."
                ),
                priority=8,
            )
        )
    else:
        findings.append(
            Finding(
                finding_id="broad_movement",
                title=f"{n_moved} of {n_total} hosts moved versus baseline",
                body=(
                    "Movement is broad rather than concentrated — check for an "
                    "external change that affected the whole fleet."
                ),
                priority=8,
            )
        )

    findings.sort(key=lambda f: -f.priority)
    return findings


def apply_finding_overrides(
    findings: list[Finding],
    overrides_text: str | None,
) -> list[Finding]:
    """Apply per-finding `headline` overrides from a wrapper slot.

    `overrides_text` is the raw `text` field of an `llm-finding-overrides`
    note — JSON `[{"finding_id": ..., "headline": ...}, ...]`. Silently
    ignores malformed payloads; deterministic titles remain in place.
    """
    if not overrides_text:
        return findings
    import json

    try:
        items = json.loads(overrides_text)
    except (ValueError, TypeError):
        return findings
    if not isinstance(items, list):
        return findings
    by_id = {f.finding_id: f for f in findings}
    for item in items:
        if not isinstance(item, dict):
            continue
        fid = item.get("finding_id")
        headline = item.get("headline")
        if fid in by_id and isinstance(headline, str) and headline.strip():
            by_id[fid].headline = headline.strip()
    return findings
