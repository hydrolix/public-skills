#!/usr/bin/env python3
"""Validate public skill structure and advisory SQL examples.

This is an authoring-time validator for this repository. It intentionally does
not create runtime dependencies between independently installed skills.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


SQL_FENCE_RE = re.compile(r"```sql\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
COMMAND_FENCE_RE = re.compile(r"```(?:bash|sh|shell|console)?\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
LOCAL_MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
README_SKILL_ROW_RE = re.compile(r"^\|\s*\[([a-z0-9-]+)\]\(skills/([a-z0-9-]+)/\)\s*\|", re.MULTILINE)
LOCAL_PYTHON_RE = re.compile(
    r"\bpython(?:3)?\s+((?:\./)?(?:scripts|skills)/[^\s`|;&]+\.py)\b"
)
SCHEMA_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|")
SUMMARY_HEADING_RE = re.compile(r"^##\s+([A-Za-z0-9_]+)\s*$")
SUMMARY_AGG_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|")
FROM_TABLE_RE = re.compile(r"\bFROM\s+([^\s;]+)", re.IGNORECASE)
PROJECT_TABLE_RE = re.compile(r"(?:<project>|__PROJECT_NAME__)\.([A-Za-z0-9_]+)")
SELECT_STAR_RE = re.compile(r"\bSELECT\s+\*", re.IGNORECASE)
STATUS_NUMERIC_COMPARE_RE = re.compile(
    r"\b(response_status_code)\s*(=|!=|<>|>=|<=|>|<)\s*([0-9]{3})\b",
    re.IGNORECASE,
)
TIMESTAMP_FILTER_RE = re.compile(
    r"\bWHERE\b[\s\S]*\btimestamp\b\s*(?:=|!=|<>|>=|<=|>|<|BETWEEN|IN)(?:\s|\(|'|$)",
    re.IGNORECASE,
)
PROVIDER_FIELD_RE = re.compile(
    r"\b("
    r"akamai_[A-Za-z0-9_]+|cloudflare_[A-Za-z0-9_]+|"
    r"cloudfront_[A-Za-z0-9_]+|fastly_[A-Za-z0-9_]+|"
    r"tencent_[A-Za-z0-9_]+|zuplo_[A-Za-z0-9_]+|"
    r"gateway_latency_ms"
    r")\b"
)
SOURCE_GUARD_RE = re.compile(r"\b(hdx_cdn|summary table|summary)\b", re.IGNORECASE)
MERGE_FUNCTION_RE = re.compile(r"\b[A-Za-z0-9_]*Merge(?:If)?\s*\(", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    severity: str
    path: Path
    line: int
    message: str


@dataclass(frozen=True)
class SqlBlock:
    path: Path
    line: int
    sql: str


@dataclass
class SkillMeta:
    path: Path
    name: str
    tables: set[str]
    summary_tables: set[str]
    aggregate_columns_by_summary: dict[str, set[str]]
    string_columns: set[str]


REQUIRED_PLUGIN_FIELDS = {
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "bugs",
    "license",
    "keywords",
}
OPENAI_METADATA_EXEMPTIONS: set[str] = set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate public skill structure and SQL examples."
    )
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path("."),
        help="Repository root. Defaults to the current directory.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors.",
    )
    return parser.parse_args()


def line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def add(
    findings: list[Finding],
    severity: str,
    path: Path,
    line: int,
    message: str,
) -> None:
    findings.append(Finding(severity, path, line, message))


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def count_lines(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") or not text else 1)


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}

    result: dict[str, str] = {}
    lines = text[4:end].splitlines()
    current_key: str | None = None
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_parts
        if current_key:
            result[current_key] = " ".join(part.strip() for part in current_parts).strip()
        current_key = None
        current_parts = []

    for line in lines:
        if line.startswith("  ") and current_key:
            current_parts.append(line.strip())
            continue
        flush()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        if value in {">", "|"}:
            value = ""
        current_parts = [value] if value else []
    flush()
    return result


def parse_openai_yaml(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    in_interface = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("interface:"):
            in_interface = True
            continue
        if not in_interface:
            continue
        if not line.startswith("  "):
            in_interface = False
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        result[key.strip()] = value.strip().strip("\"'")
    return result


def has_top_level_navigation(text: str) -> bool:
    lines = text.splitlines()[:60]
    for idx, line in enumerate(lines):
        if re.match(r"^##\s+(Contents|Table of Contents|Navigation)\s*$", line, re.IGNORECASE):
            following = "\n".join(lines[idx + 1 : idx + 8])
            return bool(re.search(r"^\s*-\s+\[[^\]]+\]\(#[^)]+\)", following, re.MULTILINE))
    return False


def validate_skill_structure(skill_dir: Path, findings: list[Finding]) -> None:
    if not SKILL_NAME_RE.fullmatch(skill_dir.name):
        add(
            findings,
            "error",
            skill_dir,
            1,
            "skill directory name must be lowercase hyphenated, start with a letter or digit, and be at most 64 characters",
        )

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        add(findings, "error", skill_dir, 1, "missing SKILL.md")
        return

    text = read_text(skill_file)
    if count_lines(text) >= 500:
        add(findings, "error", skill_file, 1, "SKILL.md must be under 500 lines")

    frontmatter = parse_frontmatter(text)
    for key in ("name", "description"):
        if not frontmatter.get(key):
            add(findings, "error", skill_file, 1, f"frontmatter missing {key!r}")

    expected_name = skill_dir.name
    actual_name = frontmatter.get("name")
    if actual_name and actual_name != expected_name:
        add(
            findings,
            "error",
            skill_file,
            1,
            f"frontmatter name {actual_name!r} does not match directory {expected_name!r}",
        )


def validate_reference_navigation(skill_dir: Path, findings: list[Finding]) -> None:
    references = skill_dir / "references"
    if not references.is_dir():
        return
    for md_path in sorted(references.rglob("*.md")):
        text = read_text(md_path)
        if count_lines(text) > 100 and not has_top_level_navigation(text):
            add(
                findings,
                "error",
                md_path,
                1,
                "reference files over 100 lines must include top-level Contents navigation",
            )


def validate_local_links(skill_dir: Path, findings: list[Finding]) -> None:
    for md_path in sorted(skill_dir.rglob("*.md")):
        text = read_text(md_path)
        for match in LOCAL_MD_LINK_RE.finditer(text):
            target = match.group(1).strip()
            if (
                "://" in target
                or target.startswith("#")
                or target.startswith("mailto:")
                or target.startswith("<")
            ):
                continue
            target_path = target.split("#", 1)[0].strip()
            if not target_path:
                continue
            resolved = (md_path.parent / target_path).resolve()
            if not resolved.exists():
                add(
                    findings,
                    "error",
                    md_path,
                    line_for_offset(text, match.start()),
                    f"broken local markdown link: {target}",
                )


def validate_repo_python_commands(skill_dir: Path, findings: list[Finding]) -> None:
    for md_path in sorted(skill_dir.rglob("*.md")):
        text = read_text(md_path)
        for fence in COMMAND_FENCE_RE.finditer(text):
            body = fence.group(1)
            for match in LOCAL_PYTHON_RE.finditer(body):
                line_start = body.rfind("\n", 0, match.start()) + 1
                command_prefix = body[max(0, match.start() - 160) : match.start()]
                if "uv run" in body[line_start : match.start()] or "uv run" in command_prefix:
                    continue
                add(
                    findings,
                    "error",
                    md_path,
                    line_for_offset(text, fence.start(1) + match.start()),
                    f"repo-local Python example should use 'uv run python': {match.group(0)}",
                )


def collect_sql_blocks(skill_dir: Path) -> list[SqlBlock]:
    blocks: list[SqlBlock] = []
    for md_path in sorted(skill_dir.rglob("*.md")):
        text = read_text(md_path)
        for match in SQL_FENCE_RE.finditer(text):
            blocks.append(
                SqlBlock(
                    path=md_path,
                    line=line_for_offset(text, match.start()),
                    sql=match.group(1).strip(),
                )
            )
    return blocks


def parse_schema(skill_dir: Path) -> tuple[set[str], set[str]]:
    schema_path = skill_dir / "references" / "schema.md"
    if not schema_path.exists():
        return set(), set()

    string_columns: set[str] = set()
    text = read_text(schema_path)
    tables = set(re.findall(r"^#\s+([A-Za-z0-9_]+)\s+[-—]", text, re.MULTILINE))

    for line in text.splitlines():
        match = SCHEMA_ROW_RE.match(line)
        if not match:
            continue
        column = match.group(1)
        column_type = match.group(2).strip().lower()
        if "string" in column_type:
            string_columns.add(column)
    return tables, string_columns


def parse_summary_tables(skill_dir: Path) -> tuple[set[str], dict[str, set[str]]]:
    summary_path = skill_dir / "references" / "summary-tables.md"
    if not summary_path.exists():
        return set(), {}

    text = read_text(summary_path)
    summaries: set[str] = set()
    aggregates_by_summary: dict[str, set[str]] = {}
    current: str | None = None
    in_aggregates = False

    for line in text.splitlines():
        heading = SUMMARY_HEADING_RE.match(line)
        if heading:
            current = heading.group(1)
            summaries.add(current)
            aggregates_by_summary.setdefault(current, set())
            in_aggregates = False
            continue
        if line.startswith("### "):
            in_aggregates = line.strip().lower() == "### aggregates"
            continue
        if not current or not in_aggregates:
            continue
        row = SUMMARY_AGG_ROW_RE.match(line)
        if row:
            column = row.group(1)
            if column.lower() != "column":
                aggregates_by_summary[current].add(column)

    return summaries, aggregates_by_summary


def build_skill_meta(skill_dir: Path) -> SkillMeta:
    tables, string_columns = parse_schema(skill_dir)
    summary_tables, aggregates_by_summary = parse_summary_tables(skill_dir)
    return SkillMeta(
        path=skill_dir,
        name=skill_dir.name,
        tables=tables | summary_tables,
        summary_tables=summary_tables,
        aggregate_columns_by_summary=aggregates_by_summary,
        string_columns=string_columns,
    )


def table_refs(sql: str) -> set[str]:
    refs: set[str] = set()
    for match in FROM_TABLE_RE.finditer(sql):
        token = match.group(1).strip("`(),")
        project_match = PROJECT_TABLE_RE.search(token)
        if project_match:
            refs.add(project_match.group(1))
            continue
        if "." in token:
            refs.add(token.rsplit(".", 1)[-1].strip("`"))
    return refs


def is_summary_definition(block: SqlBlock) -> bool:
    return block.path.name == "summary-tables.md" and "__PROJECT_NAME__" in block.sql


def has_summary_merge(sql: str) -> bool:
    return bool(MERGE_FUNCTION_RE.search(sql))


def validate_sql_blocks(
    meta: SkillMeta,
    blocks: list[SqlBlock],
    findings: list[Finding],
) -> None:
    for block in blocks:
        sql = block.sql
        refs = table_refs(sql)

        if SELECT_STAR_RE.search(sql):
            add(
                findings,
                "warning",
                block.path,
                block.line,
                "SQL example uses SELECT *; examples should project needed columns",
            )

        if (
            refs & meta.tables
            and not is_summary_definition(block)
            and not TIMESTAMP_FILTER_RE.search(sql)
        ):
            add(
                findings,
                "warning",
                block.path,
                block.line,
                "SQL example references bundle tables without an explicit timestamp filter",
            )

        for match in STATUS_NUMERIC_COMPARE_RE.finditer(sql):
            column = match.group(1)
            if column in meta.string_columns:
                add(
                    findings,
                    "warning",
                    block.path,
                    block.line + line_for_offset(sql, match.start()) - 1,
                    f"{column} is documented as string but compared to numeric literal {match.group(3)}",
                )

        for summary_table in refs & meta.summary_tables:
            aggregates = meta.aggregate_columns_by_summary.get(summary_table, set())
            if not aggregates:
                continue
            used_aggregate_columns = {
                column
                for column in aggregates
                if re.search(rf"\b{re.escape(column)}\b", sql)
            }
            if used_aggregate_columns and not has_summary_merge(sql):
                add(
                    findings,
                    "warning",
                    block.path,
                    block.line,
                    f"summary table {summary_table} aggregate columns should use -Merge functions",
                )

        provider_fields = sorted(
            field
            for field in set(PROVIDER_FIELD_RE.findall(sql))
            if field not in meta.tables
        )
        if provider_fields and not (refs & meta.summary_tables) and not SOURCE_GUARD_RE.search(sql):
            add(
                findings,
                "warning",
                block.path,
                block.line,
                "provider/source-specific fields appear without an hdx_cdn filter or summary-table context: "
                + ", ".join(provider_fields[:5]),
            )


def validate_skill(skill_dir: Path, findings: list[Finding]) -> None:
    validate_skill_structure(skill_dir, findings)
    if not (skill_dir / "SKILL.md").exists():
        return
    validate_local_links(skill_dir, findings)
    validate_reference_navigation(skill_dir, findings)
    validate_repo_python_commands(skill_dir, findings)
    meta = build_skill_meta(skill_dir)
    validate_sql_blocks(meta, collect_sql_blocks(skill_dir), findings)


def read_skill_frontmatter(skill_dir: Path) -> dict[str, str]:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return {}
    return parse_frontmatter(read_text(skill_file))


def validate_readme_skill_table(root: Path, skill_dirs: list[Path], findings: list[Finding]) -> None:
    readme = root / "README.md"
    if not readme.exists():
        add(findings, "error", readme, 1, "missing README.md")
        return

    text = read_text(readme)
    rows = README_SKILL_ROW_RE.findall(text)
    listed = {name for name, path_name in rows if name == path_name}
    mismatched = [(name, path_name) for name, path_name in rows if name != path_name]
    for name, path_name in mismatched:
        add(findings, "error", readme, 1, f"README skill row label {name!r} does not match path {path_name!r}")

    actual = {skill_dir.name for skill_dir in skill_dirs}
    for name in sorted(actual - listed):
        add(findings, "error", readme, 1, f"README skill table missing {name!r}")
    for name in sorted(listed - actual):
        add(findings, "error", readme, 1, f"README skill table lists missing skill directory {name!r}")


def validate_plugin_metadata(root: Path, skill_dirs: list[Path], findings: list[Finding]) -> None:
    plugin_path = root / ".claude-plugin" / "plugin.json"
    if not plugin_path.exists():
        add(findings, "error", plugin_path, 1, "missing .claude-plugin/plugin.json")
        return

    try:
        plugin = json.loads(read_text(plugin_path))
    except json.JSONDecodeError as exc:
        add(findings, "error", plugin_path, exc.lineno, f"invalid JSON: {exc.msg}")
        return

    missing = sorted(field for field in REQUIRED_PLUGIN_FIELDS if not plugin.get(field))
    if missing:
        add(findings, "error", plugin_path, 1, "plugin metadata missing required field(s): " + ", ".join(missing))

    keywords = {str(item).lower() for item in plugin.get("keywords", []) if isinstance(item, str)}
    description = str(plugin.get("description", "")).lower()
    searchable = " ".join(sorted(keywords)) + " " + description
    for skill_dir in skill_dirs:
        tokens = {part for part in skill_dir.name.split("-") if len(part) > 2}
        if tokens and not any(token in searchable for token in tokens):
            add(
                findings,
                "warning",
                plugin_path,
                1,
                f"plugin description/keywords may not cover skill {skill_dir.name!r}",
            )


def validate_openai_metadata(skill_dirs: list[Path], findings: list[Finding]) -> None:
    for skill_dir in skill_dirs:
        if skill_dir.name in OPENAI_METADATA_EXEMPTIONS:
            continue
        path = skill_dir / "agents" / "openai.yaml"
        if not path.exists():
            add(findings, "error", skill_dir, 1, "missing agents/openai.yaml")
            continue
        values = parse_openai_yaml(read_text(path))
        required = ("display_name", "short_description", "default_prompt")
        for key in required:
            if not values.get(key):
                add(findings, "error", path, 1, f"agents/openai.yaml missing interface.{key}")
        prompt = values.get("default_prompt", "")
        if prompt and f"${skill_dir.name}" not in prompt:
            add(
                findings,
                "error",
                path,
                1,
                f"default_prompt must reference ${skill_dir.name}",
            )


def expected_site_skills(root: Path, skill_dirs: list[Path]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for skill_dir in skill_dirs:
        meta = read_skill_frontmatter(skill_dir)
        ref_dir = skill_dir / "references"
        ref_count = (
            len([path for path in ref_dir.rglob("*") if path.is_file()])
            if ref_dir.is_dir()
            else 0
        )
        items.append(
            {
                "name": skill_dir.name,
                "description": meta.get("description", ""),
                "filename": f"{skill_dir.name}.zip",
                "downloadUrl": f"./{skill_dir.name}.zip",
                "referenceFiles": ref_count,
            }
        )
    return items


def validate_site(root: Path, skill_dirs: list[Path], findings: list[Finding]) -> None:
    script = root / "scripts" / "generate-site.sh"
    if not script.exists():
        add(findings, "error", script, 1, "missing scripts/generate-site.sh")
        return

    with TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        subprocess.run(
            ["cp", "-R", str(root / "skills"), str(tmp_root / "skills")],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["cp", "-R", str(root / "scripts"), str(tmp_root / "scripts")],
            check=True,
            capture_output=True,
            text=True,
        )
        env = os.environ.copy()
        env["SOURCE_DATE_EPOCH"] = "0"
        generated = subprocess.run(
            ["bash", "scripts/generate-site.sh"],
            cwd=tmp_root,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if generated.returncode != 0:
            add(findings, "error", script, 1, "scripts/generate-site.sh failed during validation")
            return

        site_json = tmp_root / "site" / "skills.json"
        site_index = tmp_root / "site" / "index.html"
        if not site_json.exists():
            add(findings, "error", script, 1, "scripts/generate-site.sh did not create site/skills.json")
            return
        if not site_index.exists():
            add(findings, "error", script, 1, "scripts/generate-site.sh did not create site/index.html")
            return

        try:
            actual = json.loads(read_text(site_json))
        except json.JSONDecodeError as exc:
            add(findings, "error", script, exc.lineno, f"generated site/skills.json is invalid JSON: {exc.msg}")
            return

        expected_skills = expected_site_skills(root, skill_dirs)
        if actual.get("skills") != expected_skills:
            add(
                findings,
                "error",
                script,
                1,
                "generated site/skills.json does not match current skills",
            )

        index_text = read_text(site_index)
        for skill_dir in skill_dirs:
            if skill_dir.name not in index_text:
                add(
                    findings,
                    "error",
                    script,
                    1,
                    f"generated site/index.html missing skill {skill_dir.name!r}",
                )


def print_findings(findings: list[Finding]) -> None:
    for finding in sorted(findings, key=lambda item: (item.path, item.line, item.severity)):
        print(
            f"{finding.severity.upper()}: "
            f"{display_path(finding.path)}:{finding.line}: {finding.message}"
        )


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    skills_dir = root / "skills"
    findings: list[Finding] = []
    skill_dirs: list[Path] = []

    if not skills_dir.is_dir():
        add(findings, "error", skills_dir, 1, "missing skills/ directory")
    else:
        skill_dirs = [
            skill_dir
            for skill_dir in sorted(skills_dir.iterdir())
            if skill_dir.is_dir() and not skill_dir.name.startswith(".")
        ]
        for skill_dir in skill_dirs:
            validate_skill(skill_dir, findings)

    validate_readme_skill_table(root, skill_dirs, findings)
    validate_plugin_metadata(root, skill_dirs, findings)
    validate_openai_metadata(skill_dirs, findings)
    validate_site(root, skill_dirs, findings)

    print_findings(findings)

    errors = sum(1 for finding in findings if finding.severity == "error")
    warnings = sum(1 for finding in findings if finding.severity == "warning")
    print(f"Validation complete: {errors} error(s), {warnings} warning(s)")

    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
