#!/usr/bin/env python3
"""Generate schema reference docs from Hydrolix bundle definitions.

Reads implicit-schema.json files (merged field inventories) and bundle.json
files (summary tables, functions) to produce references/*.md for each skill.

Usage:
    python scripts/generate-schema.py \
        --schemas-dir /path/to/catalog-content-live-dashboard-validation/bundles \
        --bundles-dir /path/to/solution-bundles/bundles \
        --output-dir skills

    # Generate for a single bundle
    python scripts/generate-schema.py \
        --schemas-dir ... --bundles-dir ... --output-dir skills \
        --bundle bot-insights
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Semantic groupings for field categorization.
# Order matters — first match wins.
FIELD_CATEGORIES = [
    ("Timestamp", ["timestamp", "time", "date"]),
    ("Request", ["request_", "req_", "method", "url", "path", "query_string", "protocol", "referrer", "referer"]),
    ("Response", ["response_", "resp_", "status_code", "content_type"]),
    ("Cache", ["cache_", "cached"]),
    ("Origin", ["origin_"]),
    ("Client", ["client_", "viewer_", "remote_"]),
    ("Geo", ["country", "city", "region", "continent", "latitude", "longitude", "geo_", "asn"]),
    ("User Agent", ["user_agent", "ua_", "bot_", "crawler", "browser"]),
    ("CDN", ["cdn_", "hdx_cdn", "edge_", "pop_", "akamai_", "cloudfront_", "fastly_", "cloudflare_", "tencent_"]),
    ("Security", ["security_", "waf_", "attack_", "rule_", "threat_", "firewall_"]),
    ("Performance", ["duration", "latency", "ttfb", "ttlb", "exec_time", "bytes_read", "bandwidth"]),
    ("Identity", ["account", "user_id", "session", "token", "credential"]),
    ("Hydrolix", ["hdx_", "hydrolix_"]),
]


def categorize_field(name: str) -> str:
    lower = name.lower()
    for category, prefixes in FIELD_CATEGORIES:
        if any(lower.startswith(p) or p in lower for p in prefixes):
            return category
    return "Other"


def format_type(types: list[str]) -> str:
    if len(types) == 1:
        return types[0]
    return " / ".join(sorted(types))


def format_flags(field: dict) -> str:
    flags = []
    if field.get("primary"):
        flags.append("primary")
    if field.get("index"):
        flags.append("indexed")
    if field.get("virtual"):
        flags.append("virtual")
    if field.get("suppress"):
        flags.append("suppressed")
    return ", ".join(flags) if flags else ""


def source_label(source_path: str) -> str:
    """Extract a short label from a transform source path."""
    # "transformations/mcdn_akamai_ds2/transform.json" -> "akamai_ds2"
    match = re.search(r"transformations/(?:mcdn_)?(.+?)/transform\.json", source_path)
    if match:
        return match.group(1)
    match = re.search(r"transformations/(.+?)/transform\.json", source_path)
    if match:
        return match.group(1)
    return source_path


def generate_schema_md(bundle_id: str, table: dict) -> str:
    """Generate schema.md content for a single table."""
    fields = table["fields"]
    table_name = table["name"]

    # Group fields by category
    grouped: dict[str, list[dict]] = {}
    for field in sorted(fields, key=lambda f: f["name"]):
        cat = categorize_field(field["name"])
        grouped.setdefault(cat, []).append(field)

    lines = [
        f"# {table_name} — Column Reference",
        "",
        f"Primary table schema for the **{bundle_id}** bundle.",
        f"Total columns: {len(fields)}",
        "",
    ]

    # Stats summary
    indexed = sum(1 for f in fields if f.get("index"))
    virtual = sum(1 for f in fields if f.get("virtual"))
    suppressed = sum(1 for f in fields if f.get("suppress"))
    lines.append(f"| Indexed | Virtual | Suppressed |")
    lines.append(f"|---------|---------|------------|")
    lines.append(f"| {indexed} | {virtual} | {suppressed} |")
    lines.append("")

    for category in [cat for cat, _ in FIELD_CATEGORIES] + ["Other"]:
        cat_fields = grouped.get(category)
        if not cat_fields:
            continue

        lines.append(f"## {category}")
        lines.append("")
        lines.append("| Column | Type | Flags | Sources |")
        lines.append("|--------|------|-------|---------|")

        for field in cat_fields:
            name = field["name"]
            ftype = format_type(field.get("types", []))
            flags = format_flags(field)
            sources = field.get("sources", [])
            if len(sources) <= 3:
                source_str = ", ".join(source_label(s) for s in sources)
            else:
                source_str = f"{len(sources)} transforms"
            lines.append(f"| `{name}` | {ftype} | {flags} | {source_str} |")

        lines.append("")

    return "\n".join(lines)


def parse_summary_sql(sql_text: str) -> dict:
    """Extract dimensions and aggregates from a summary table SQL."""
    dimensions = []
    aggregates = []

    # Find GROUP BY columns
    group_match = re.search(r"GROUP\s+BY\s+(.+?)(?:SETTINGS|$)", sql_text, re.DOTALL | re.IGNORECASE)
    if group_match:
        group_block = group_match.group(1)
        for line in group_block.strip().split("\n"):
            col = line.strip().rstrip(",").strip()
            if col:
                dimensions.append(col)

    # Find aggregate expressions (lines with AS alias in the SELECT)
    select_match = re.search(r"SELECT\s+(.+?)FROM", sql_text, re.DOTALL | re.IGNORECASE)
    if select_match:
        select_block = select_match.group(1)
        for line in select_block.strip().split("\n"):
            line = line.strip().rstrip(",")
            # Match aggregate functions: count(), sum(), avg(), quantiles()
            if re.search(r"\b(count|sum|avg|min|max|quantiles?|any|uniq)\s*\(", line, re.IGNORECASE):
                alias_match = re.search(r"\bAS\s+(\w+)", line, re.IGNORECASE)
                alias = alias_match.group(1) if alias_match else line
                # Extract the function call
                func_match = re.match(r"(.+?)\s+AS\s+\w+", line, re.IGNORECASE)
                expr = func_match.group(1).strip() if func_match else line
                aggregates.append({"name": alias, "expression": expr})

    return {"dimensions": dimensions, "aggregates": aggregates}


def generate_summary_tables_md(bundle_id: str, summary_tables: list[dict], bundles_dir: Path) -> str | None:
    """Generate summary-tables.md from bundle.json summary table definitions."""
    if not summary_tables:
        return None

    lines = [
        f"# {bundle_id} — Summary Tables",
        "",
        "Pre-aggregated views for faster query performance at reduced granularity.",
        "",
        "When querying summary tables, use `-Merge` aggregate combiners to re-aggregate",
        "pre-computed values. For example, use `avgMerge(response_ttfb_ms)` instead of",
        "`avg(response_ttfb_ms)`, and `quantilesMerge(0.5)(quantiles_response_ttfb_ms)`",
        "instead of `quantile(0.5)(response_time_to_first_byte_ms)`.",
        "",
    ]

    for st in summary_tables:
        name = st.get("name", "unknown")
        parent = st.get("parent_table_name", "unknown")
        sql_path = st.get("sql", {}).get("path")

        lines.append(f"## {name}")
        lines.append("")
        lines.append(f"Parent table: `{parent}`")
        lines.append("")

        if sql_path:
            # Find the SQL file relative to the bundle directory
            # Try native first, then trafficpeak
            sql_file = None
            for variant in ["", "native", "trafficpeak"]:
                candidate = bundles_dir / variant / bundle_id / sql_path
                if candidate.exists():
                    sql_file = candidate
                    break

            if sql_file:
                sql_text = sql_file.read_text()
                parsed = parse_summary_sql(sql_text)

                if parsed["dimensions"]:
                    lines.append("### Dimensions (GROUP BY)")
                    lines.append("")
                    for dim in parsed["dimensions"]:
                        lines.append(f"- `{dim}`")
                    lines.append("")

                if parsed["aggregates"]:
                    lines.append("### Aggregates")
                    lines.append("")
                    lines.append("| Column | Expression |")
                    lines.append("|--------|------------|")
                    for agg in parsed["aggregates"]:
                        lines.append(f"| `{agg['name']}` | `{agg['expression']}` |")
                    lines.append("")

                lines.append("### SQL")
                lines.append("")
                lines.append("```sql")
                lines.append(sql_text.strip())
                lines.append("```")
                lines.append("")
            else:
                lines.append(f"SQL file: `{sql_path}` (not found)")
                lines.append("")

    return "\n".join(lines)


def generate_functions_md(bundle_id: str, functions: list[dict], bundles_dir: Path) -> str | None:
    """Generate shared-functions.md from function definition files."""
    if not functions:
        return None

    lines = [
        f"# {bundle_id} — Shared Functions",
        "",
        "SQL functions available for use in queries against this bundle's tables.",
        "These are deployed to the Hydrolix cluster as part of the bundle.",
        "",
    ]

    for fn_ref in functions:
        fn_path = fn_ref.get("path") if isinstance(fn_ref, dict) else fn_ref
        if not fn_path:
            continue

        fn_file = None
        for variant in ["", "native", "trafficpeak"]:
            candidate = bundles_dir / variant / bundle_id / fn_path
            if candidate.exists():
                fn_file = candidate
                break

        if fn_file:
            if fn_file.suffix == ".sql":
                # Raw SQL function definition
                name = fn_file.stem
                sql = fn_file.read_text().strip()
                lines.append(f"## `{name}`")
                lines.append("")
                lines.append("```sql")
                lines.append(sql)
                lines.append("```")
                lines.append("")
            else:
                try:
                    fn_data = json.loads(fn_file.read_text())
                    name = fn_data.get("name", fn_path)
                    desc = fn_data.get("description", "")
                    sql = fn_data.get("sql", "")

                    lines.append(f"## `{name}`")
                    lines.append("")
                    if desc:
                        lines.append(desc)
                        lines.append("")
                    if sql:
                        lines.append("```sql")
                        lines.append(sql.strip())
                        lines.append("```")
                        lines.append("")
                except (json.JSONDecodeError, KeyError):
                    lines.append(f"## {fn_path}")
                    lines.append("")
                    lines.append("(could not parse function definition)")
                    lines.append("")

    # Only return if we found at least one function
    if len(lines) <= 5:
        return None
    return "\n".join(lines)


def scan_functions_dir(bundle_id: str, bundles_dir: Path) -> list[dict]:
    """Scan the functions/ directory for JSON and SQL function definitions."""
    fns = []
    for variant in ["", "native", "trafficpeak"]:
        fn_dir = bundles_dir / variant / bundle_id / "functions"
        if fn_dir.is_dir():
            for fn_file in sorted(fn_dir.glob("*.json")):
                fns.append({"path": f"functions/{fn_file.name}"})
            for fn_file in sorted(fn_dir.glob("*.sql")):
                fns.append({"path": f"functions/{fn_file.name}"})
            if fns:
                break
    return fns


def load_bundle_json(bundle_id: str, bundles_dir: Path) -> dict | None:
    """Try to find bundle.json for a given bundle ID.

    Searches: direct path, native/, trafficpeak/ subdirectories.
    """
    candidates = [
        bundles_dir / bundle_id / "bundle.json",
        bundles_dir / "native" / bundle_id / "bundle.json",
        bundles_dir / "trafficpeak" / bundle_id / "bundle.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return json.loads(candidate.read_text())
    return None


def process_bundle(
    bundle_id: str,
    schemas_dir: Path,
    bundles_dir: Path,
    output_dir: Path,
) -> None:
    # Look up schema using mapped name if needed
    schema_name = BUNDLE_TO_SCHEMA.get(bundle_id, bundle_id)
    schema_file = schemas_dir / schema_name / "implicit-schema.json"
    if not schema_file.exists():
        print(f"  SKIP {bundle_id}: no implicit-schema.json", file=sys.stderr)
        return

    schema = json.loads(schema_file.read_text())
    skill_dir = output_dir / bundle_id / "references"
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Generate primary table schema(s)
    tables = schema.get("tables", [])
    if len(tables) == 1:
        schema_md = generate_schema_md(bundle_id, tables[0])
        out_file = skill_dir / "schema.md"
        out_file.write_text(schema_md)
        print(f"  {out_file}: {len(tables[0]['fields'])} fields")
    else:
        # Multiple tables — combine into one schema.md
        parts = []
        total_fields = 0
        for table in tables:
            parts.append(generate_schema_md(bundle_id, table))
            total_fields += len(table["fields"])
        out_file = skill_dir / "schema.md"
        out_file.write_text("\n---\n\n".join(parts))
        print(f"  {out_file}: {len(tables)} tables, {total_fields} fields")

    # Load bundle.json for summary tables and functions
    bundle = load_bundle_json(bundle_id, bundles_dir)
    if bundle:
        # Summary tables
        summary_tables = bundle.get("summary_tables", [])
        summary_md = generate_summary_tables_md(bundle_id, summary_tables, bundles_dir)
        if summary_md:
            out_file = skill_dir / "summary-tables.md"
            out_file.write_text(summary_md)
            print(f"  {out_file}: {len(summary_tables)} summary tables")

        # Functions — from dependencies or by scanning functions/ directory
        deps = bundle.get("dependencies", {}).get("hydrolix", {})
        all_fns = deps.get("functions", {}).get("shared", []) + deps.get("functions", {}).get("bundle", [])

        # If no functions declared in dependencies, scan the functions/ directory
        if not all_fns:
            all_fns = scan_functions_dir(bundle_id, bundles_dir)

        fns_md = generate_functions_md(bundle_id, all_fns, bundles_dir)
        if fns_md:
            out_file = skill_dir / "shared-functions.md"
            out_file.write_text(fns_md)
            print(f"  {out_file}: {len(all_fns)} functions")
    else:
        print(f"  {bundle_id}: no bundle.json found (schema only)")


# Mapping from schema repo names to canonical skill/bundle names.
# When the implicit-schema.json directory name differs from the bundle.json directory name.
SCHEMA_TO_BUNDLE = {
    "cdn-bot-detection": "bot-detection",
}

# Reverse: canonical name -> schema repo name
BUNDLE_TO_SCHEMA = {v: k for k, v in SCHEMA_TO_BUNDLE.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate schema reference docs from Hydrolix bundle definitions")
    parser.add_argument(
        "--schemas-dir",
        type=Path,
        required=True,
        help="Path to catalog-content-live-dashboard-validation/bundles/",
    )
    parser.add_argument(
        "--bundles-dir",
        type=Path,
        required=True,
        help="Path to solution-bundles/bundles/",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Path to skills/ output directory",
    )
    parser.add_argument(
        "--bundle",
        type=str,
        default=None,
        help="Generate for a single bundle (default: all)",
    )
    args = parser.parse_args()

    if not args.schemas_dir.exists():
        print(f"ERROR: schemas dir not found: {args.schemas_dir}", file=sys.stderr)
        sys.exit(1)

    if args.bundle:
        bundle_ids = [args.bundle]
    else:
        # Collect all schema dirs, applying name mapping
        bundle_ids = []
        for d in sorted(args.schemas_dir.iterdir()):
            if d.is_dir() and (d / "implicit-schema.json").exists():
                canonical = SCHEMA_TO_BUNDLE.get(d.name, d.name)
                if canonical not in bundle_ids:
                    bundle_ids.append(canonical)
        bundle_ids.sort()

    print(f"Generating schemas for {len(bundle_ids)} bundles")
    for bundle_id in bundle_ids:
        print(f"\n{bundle_id}:")
        process_bundle(bundle_id, args.schemas_dir, args.bundles_dir, args.output_dir)

    print("\nDone.")


if __name__ == "__main__":
    main()
