# Contributing

This repository publishes Hydrolix skills for AI assistants. Contributions
should keep skills compact, deterministic where needed, and easy to package for
multiple assistant runtimes.

## Skill Anatomy

Each skill lives under `skills/<skill-name>/` and must include:

- `SKILL.md`: required runtime instructions with YAML frontmatter.
- `agents/openai.yaml`: required UI metadata for OpenAI skill surfaces.
- `scripts/`: optional deterministic helpers for fragile or repeated work.
- `references/`: optional documentation loaded only when needed.
- `assets/`: optional templates or files used as output resources.

Skill names must be lowercase hyphenated identifiers, use only letters, digits,
and hyphens, and stay under 64 characters. The `SKILL.md` frontmatter `name`
must match the directory name exactly.

## Authoring Style

Skills are context budget, not product documentation. Assume the agent already
knows general software and writing patterns; include only the procedural,
domain, schema, or safety context it needs for this Hydrolix workflow.

Keep `SKILL.md` under 500 lines. Move detailed examples, schemas, query
patterns, variants, and long explanations into `references/` files. Reference
files over 100 lines must include a top-level `## Contents` section near the
top with links to major sections.

Use progressive disclosure:

- Put trigger guidance, key boundaries, and the decision flow in `SKILL.md`.
- Link directly from `SKILL.md` to every reference file that may be needed.
- Tell the agent when to open each reference.
- Avoid deeply nested reference paths that require hunting.

Public README files inside a skill are allowed only when they are public-facing
package documentation, examples, or rendered artifacts. Do not add internal
process notes, installation guides, or change logs inside skill folders.

## Metadata

Keep every metadata surface current when a skill changes:

- `SKILL.md` frontmatter must include `name` and `description`.
- Public frontmatter metadata such as `license`, `version`, `author`, or
  `bundle` is allowed.
- `.claude-plugin/plugin.json` must remain valid JSON with package fields,
  broad description text, and keywords covering the published skills.
- `skills/<skill>/agents/openai.yaml` must include `display_name`,
  `short_description`, and `default_prompt`.
- `default_prompt` must reference the correct skill token, for example
  `$bot-insights`.
- The root `README.md` skill table must list every skill exactly once.
- The generated Pages site must build from current skill metadata; `site/` is
  generated output and should not be committed.

## Commands

Use `uv run python` for repository-local Python commands in docs, examples,
validation steps, and workflows. Do not document bare `python` or `python3`
commands for this repo unless the example is intentionally about a host that
does not provide `uv`.

Prefer small deterministic scripts when an operation is fragile, frequently
repeated, or likely to be reimplemented inconsistently by agents.

## Validation

Run these checks before opening a pull request:

```bash
uv run python scripts/validate-skill-examples.py --strict
uv run python -m unittest discover -s tests
scripts/generate-site.sh
```

The strict validator checks skill structure, frontmatter, metadata consistency,
Markdown links, long-reference navigation, repo-local Python command style,
generated site output, and Hydrolix SQL example guardrails.
