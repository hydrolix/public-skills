#!/usr/bin/env python3
"""Compute bot-insights current/baseline deltas from simple metric JSON.

Input can be either:

  {"current": {"requests": 120}, "baseline": {"requests": 80}}

or a list of rows with a period field:

  [{"period": "current", "requests": 120}, {"period": "baseline", "requests": 80}]

The percentage formula intentionally matches the skill references:
(current - baseline) / greatest(baseline, 1) * 100.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _load_baselines_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_bot_insights_baselines", Path(__file__).with_name("baselines.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load sibling baselines.py module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baselines = _load_baselines_module()
pct_delta = baselines.pct_delta
to_number = baselines.to_number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute current/baseline metric deltas for bot-insights results."
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="Metric JSON. If omitted, stdin is read.",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Read metric JSON from a file instead of positional arguments/stdin.",
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


def rows_to_periods(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict) and "current" in value and "baseline" in value:
        current = value["current"]
        baseline = value["baseline"]
        if isinstance(current, dict) and isinstance(baseline, dict):
            return {"current": current, "baseline": baseline}

    if isinstance(value, list):
        periods: dict[str, dict[str, Any]] = {}
        for row in value:
            if not isinstance(row, dict):
                continue
            period = str(row.get("period", "")).lower()
            if period in {"current", "baseline"}:
                periods[period] = row
        if "current" in periods and "baseline" in periods:
            return periods

    raise ValueError(
        "Input must contain current and baseline objects, or rows with period=current/baseline."
    )


def compare(value: Any) -> list[dict[str, Any]]:
    periods = rows_to_periods(value)
    current = periods["current"]
    baseline = periods["baseline"]
    metric_names = sorted(set(current) & set(baseline) - {"period"})

    results: list[dict[str, Any]] = []
    for metric in metric_names:
        current_value = to_number(current[metric])
        baseline_value = to_number(baseline[metric])
        if current_value is None or baseline_value is None:
            continue
        delta = current_value - baseline_value
        pct_change = pct_delta(current_value, baseline_value)
        results.append(
            {
                "metric": metric,
                "current": current_value,
                "baseline": baseline_value,
                "absolute_delta": delta,
                "pct_change": pct_change,
            }
        )
    return results


def print_text(results: list[dict[str, Any]]) -> None:
    if not results:
        print("No comparable numeric metrics found.")
        return
    for row in results:
        print(
            f"{row['metric']}: current={row['current']:g}, "
            f"baseline={row['baseline']:g}, "
            f"delta={row['absolute_delta']:+g}, "
            f"pct_change={row['pct_change']:+.2f}%"
        )


def main() -> int:
    args = parse_args()
    try:
        results = compare(json.loads(read_input(args)))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        print_text(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
