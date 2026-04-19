#!/usr/bin/env python3
"""Summarize Hydrolix X-HDX-Query-Stats or hdx.active_queries.query_stats JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


BYTE_FIELD_RE = re.compile(r"(?:^|_)(bytes?|size|memory)(?:_|$)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize Hydrolix query stats JSON from stdin or a file."
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="Stats JSON. If omitted, stdin is read.",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Read stats from a file instead of positional arguments/stdin.",
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


def extract_json_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise ValueError("No input supplied.")

    if ":" in stripped and not stripped.startswith(("{", "[")):
        header_name, header_value = stripped.split(":", 1)
        if header_name.lower().strip() in {"x-hdx-query-stats", "query_stats"}:
            stripped = header_value.strip()

    first_obj = stripped.find("{")
    first_arr = stripped.find("[")
    starts = [idx for idx in (first_obj, first_arr) if idx >= 0]
    if starts and min(starts) > 0:
        stripped = stripped[min(starts) :]

    return stripped


def parse_stats(text: str) -> Any:
    value: Any = json.loads(extract_json_text(text))
    # query_stats sometimes arrives as a JSON-encoded string.
    while isinstance(value, str):
        value = json.loads(value)
    return value


def to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def get_path(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def format_count(value: Any) -> str:
    number = to_number(value)
    if number is None:
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def format_bytes(value: Any) -> str:
    number = to_number(value)
    if number is None:
        return str(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    idx = 0
    while abs(number) >= 1024 and idx < len(units) - 1:
        number /= 1024
        idx += 1
    if idx == 0:
        return f"{int(number)} {units[idx]}"
    return f"{number:.2f} {units[idx]}"


def collect_numeric_fields(value: Any, prefix: str = "") -> dict[str, float]:
    fields: dict[str, float] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            fields.update(collect_numeric_fields(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            fields.update(collect_numeric_fields(child, f"{prefix}[{index}]"))
    else:
        number = to_number(value)
        if number is not None:
            fields[prefix] = number
    return fields


def sum_byte_fields(fields: dict[str, float], needle: str) -> float:
    return sum(
        value
        for key, value in fields.items()
        if needle in key.lower() and BYTE_FIELD_RE.search(key)
    )


def summarize(stats: Any) -> dict[str, Any]:
    if isinstance(stats, list):
        if not stats:
            raise ValueError("Stats array is empty.")
        stats = stats[0]
    if not isinstance(stats, dict):
        raise ValueError("Stats input must decode to a JSON object or array.")
    if "query_stats" in stats and len(stats) == 1:
        stats = parse_stats(str(stats["query_stats"]))
        if not isinstance(stats, dict):
            raise ValueError("query_stats field did not decode to a JSON object.")

    runtime = get_path(stats, "query_detail_runtime_stats") or {}
    index_stats = get_path(stats, "index_stats") or {}
    numeric_runtime = collect_numeric_fields(runtime)

    cached_bytes = sum_byte_fields(numeric_runtime, "cached_")
    net_bytes = sum_byte_fields(numeric_runtime, "net_")

    base = {
        "exec_time_ms": first_present(stats, "exec_time", "exec_time_ms", "elapsed_ms"),
        "rows_read": first_present(stats, "rows_read", "read_rows"),
        "bytes_read": first_present(stats, "bytes_read", "read_bytes"),
        "num_partitions": first_present(stats, "num_partitions", "partitions"),
        "num_peers": first_present(stats, "num_peers", "peers"),
        "memory_usage": first_present(stats, "memory_usage", "peak_memory_usage"),
        "query_attempts": first_present(stats, "query_attempts"),
        "pool_name": first_present(stats, "pool_name"),
        "limit_optimization": first_present(stats, "limit_optimization"),
    }

    warnings: list[str] = []
    num_partitions = to_number(base["num_partitions"])
    query_attempts = to_number(base["query_attempts"])
    limit_optimization = base["limit_optimization"]

    if num_partitions is not None and num_partitions > 1000:
        warnings.append("High partition count; check primary timestamp pruning.")
    if query_attempts is not None and query_attempts > 1:
        warnings.append("Query retried; inspect peer health or retryable failures.")
    if net_bytes > cached_bytes and net_bytes > 0:
        warnings.append("Network reads exceed cache reads; cache miss may dominate.")
    if not limit_optimization:
        warnings.append("No limit optimization reported.")

    return {
        "basic": base,
        "runtime": {
            "cached_bytes": int(cached_bytes),
            "net_bytes": int(net_bytes),
            "hdx_blocks_skipped": first_present(runtime, "hdx_blocks_skipped"),
            "hdx_blocks_read": first_present(runtime, "hdx_blocks_read"),
        },
        "index_stats": {
            "columns_read": index_stats.get("columns_read"),
            "indexes_used": index_stats.get("indexes_used"),
            "shard_key_values_used": index_stats.get("shard_key_values_used"),
        },
        "warnings": warnings,
    }


def print_text(summary: dict[str, Any]) -> None:
    basic = summary["basic"]
    runtime = summary["runtime"]
    index_stats = summary["index_stats"]

    print("Hydrolix Query Stats Summary")
    print(f"Execution time: {value_or_missing(basic['exec_time_ms'], format_count, ' ms')}")
    print(f"Rows read: {value_or_missing(basic['rows_read'], format_count)}")
    print(f"Bytes read: {value_or_missing(basic['bytes_read'], format_bytes)}")
    print(f"Partitions: {value_or_missing(basic['num_partitions'], format_count)}")
    print(f"Peers: {value_or_missing(basic['num_peers'], format_count)}")
    print(f"Memory: {value_or_missing(basic['memory_usage'], format_bytes)}")
    print(f"Query attempts: {value_or_missing(basic['query_attempts'], format_count)}")
    print(f"Pool: {basic['pool_name'] or 'not reported'}")
    print(f"Limit optimization: {basic['limit_optimization'] or 'not reported'}")
    print(f"Cached runtime bytes: {format_bytes(runtime['cached_bytes'])}")
    print(f"Network runtime bytes: {format_bytes(runtime['net_bytes'])}")

    if runtime["hdx_blocks_skipped"] is not None:
        print(f"HDX blocks skipped: {format_count(runtime['hdx_blocks_skipped'])}")
    if runtime["hdx_blocks_read"] is not None:
        print(f"HDX blocks read: {format_count(runtime['hdx_blocks_read'])}")

    if index_stats["columns_read"] is not None:
        print(f"Columns read: {index_stats['columns_read']}")
    if index_stats["indexes_used"] is not None:
        print(f"Indexes used: {index_stats['indexes_used']}")
    if index_stats["shard_key_values_used"] is not None:
        print(f"Shard key values used: {index_stats['shard_key_values_used']}")

    if summary["warnings"]:
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"- {warning}")


def value_or_missing(
    value: Any,
    formatter: Any,
    suffix: str = "",
) -> str:
    if value is None or value == "":
        return "not reported"
    return f"{formatter(value)}{suffix}"


def main() -> int:
    args = parse_args()
    try:
        summary = summarize(parse_stats(read_input(args)))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_text(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
