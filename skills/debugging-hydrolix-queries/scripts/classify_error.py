#!/usr/bin/env python3
"""Classify common Hydrolix query errors.

Reads an error fragment from stdin, a file, or command-line arguments and
prints the likely root cause plus the first action to try.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ErrorRule:
    category: str
    patterns: tuple[str, ...]
    root_cause: str
    first_action: str
    reference_file: str = "references/error-taxonomy.md"


RULES: tuple[ErrorRule, ...] = (
    ErrorRule(
        category="execution_timeout",
        patterns=(r"Code:\s*159\b", r"Timeout exceeded: elapsed .* maximum"),
        root_cause="hdx_query_max_execution_time circuit breaker.",
        first_action=(
            "Narrow the primary timestamp range, add indexed filters, or raise "
            "the per-query timeout only if the query is legitimate."
        ),
    ),
    ErrorRule(
        category="memory_limit",
        patterns=(r"Code:\s*241\b", r"Memory limit .* exceeded"),
        root_cause="Per-query RAM cap on the query head or a query peer.",
        first_action=(
            "Try spill-to-disk for this query, reduce GROUP BY cardinality, "
            "and replace SELECT * with explicit columns."
        ),
        reference_file="references/circuit-breakers.md",
    ),
    ErrorRule(
        category="max_columns_to_read",
        patterns=(
            r"Code:\s*161\b",
            r"Limit for number of columns to read exceeded",
        ),
        root_cause="hdx_query_max_columns_to_read, usually from SELECT *.",
        first_action="Project only needed columns, or raise the caller's limit.",
    ),
    ErrorRule(
        category="max_result_rows",
        patterns=(r"Code:\s*396\b", r"Limit for result exceeded.*max rows"),
        root_cause="hdx_query_max_result_rows; response has too many rows.",
        first_action=(
            "Add LIMIT for raw-row output or pre-aggregate. Raise only if the "
            "client and cluster can handle the response."
        ),
    ),
    ErrorRule(
        category="max_result_bytes",
        patterns=(r"Code:\s*396\b", r"Limit for result exceeded.*max bytes"),
        root_cause="hdx_query_max_result_bytes; response payload is too large.",
        first_action="Prefer aggregation over raw-row dumps.",
    ),
    ErrorRule(
        category="max_timerange",
        patterns=(r"HdxStorageError", r"Maximum time range exceeded for query"),
        root_cause="hdx_query_max_timerange_sec.",
        first_action=(
            "Shorten the primary timestamp filter, or raise the limit if the "
            "wider scan is intended."
        ),
    ),
    ErrorRule(
        category="timerange_required",
        patterns=(r"HdxStorageError", r"hdx_query_timerange_required is set to true"),
        root_cause="No filter on the primary timestamp column.",
        first_action=(
            "Add a primary timestamp predicate such as "
            "WHERE <primary_ts> >= ... AND <primary_ts> < ...."
        ),
    ),
    ErrorRule(
        category="max_partitions",
        patterns=(r"HdxStorageError", r"Maximum number of partitions exceeded"),
        root_cause="hdx_query_max_partitions.",
        first_action=(
            "Tighten the time range or add a shard-key filter so the planner "
            "can prune partitions."
        ),
    ),
    ErrorRule(
        category="max_rows_scanned",
        patterns=(r"HdxStorageError", r"Maximum number of rows exceeded"),
        root_cause="hdx_query_max_rows; rows scanned, not rows returned.",
        first_action=(
            "Add a more selective WHERE clause, or use a matching summary table "
            "if one exists."
        ),
    ),
    ErrorRule(
        category="no_peers_available",
        patterns=(r"HdxStorageError", r"No peers available to run query in pool"),
        root_cause="Target query pool is empty or scaled to zero.",
        first_action="Check hdx_query_pool_name and the pool's query-peer replicas.",
        reference_file="references/circuit-breakers.md",
    ),
    ErrorRule(
        category="pool_missing",
        patterns=(r"ClusterError", r"Pool name .* does not exist"),
        root_cause="The query pool name is wrong or the pool was renamed.",
        first_action="List pools and fix hdx_query_pool_name.",
        reference_file="references/circuit-breakers.md",
    ),
    ErrorRule(
        category="catalog_timeout",
        patterns=(
            r"CatalogError",
            r"canceling statement due to statement timeout",
        ),
        root_cause="hdx_query_catalog_timeout_ms; catalog lookup was slow.",
        first_action=(
            "Retry. If it recurs, raise it with the cluster operator because "
            "the catalog needs attention."
        ),
    ),
    ErrorRule(
        category="missing_database_or_table",
        patterns=(
            r"DB::Exception: Database .* doesn't exist",
            r"Table _local\.[A-Za-z0-9_]+ does not exist",
        ),
        root_cause="Missing project qualifier, typo, or quoting issue.",
        first_action=(
            "Fully qualify as project.table and backtick hyphenated names."
        ),
    ),
    ErrorRule(
        category="network_or_infra",
        patterns=(r"DB::NetException", r"(connect timed out|No route to host)"),
        root_cause="Infrastructure-level connectivity issue.",
        first_action=(
            "Retry. If persistent, escalate to the cluster operator; this is "
            "rarely a query-authoring problem."
        ),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify common Hydrolix query error fragments."
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="Error text. If omitted, stdin is read.",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Read error text from a file instead of positional arguments/stdin.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    return parser.parse_args()


def read_input(args: argparse.Namespace) -> str:
    if args.file:
        return args.file.read_text(encoding="utf-8")
    if args.text:
        return " ".join(args.text)
    return sys.stdin.read()


def first_lines(text: str, limit: int = 2) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:limit]) if lines else text.strip()


def classify(text: str) -> dict[str, object]:
    haystack = first_lines(text) or text.strip()
    for rule in RULES:
        if all(re.search(pattern, haystack, re.IGNORECASE | re.DOTALL) for pattern in rule.patterns):
            result = asdict(rule)
            result["matched"] = True
            result["matched_text"] = haystack
            return result

    return {
        "matched": False,
        "category": "unknown",
        "root_cause": "No bundled rule matched the supplied error fragment.",
        "first_action": (
            "Read the first 1-2 error lines, inspect hdx.active_queries if the "
            "query is recent, and consult references/error-taxonomy.md."
        ),
        "reference_file": "references/error-taxonomy.md",
        "matched_text": haystack,
    }


def print_text(result: dict[str, object]) -> None:
    status = "Matched" if result["matched"] else "No match"
    print(f"{status}: {result['category']}")
    print(f"Root cause: {result['root_cause']}")
    print(f"First action: {result['first_action']}")
    print(f"Reference: {result['reference_file']}")


def main() -> int:
    args = parse_args()
    result = classify(read_input(args))
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
