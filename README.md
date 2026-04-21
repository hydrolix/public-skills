# Hydrolix Public Skills

Public skills for AI assistants built by [Hydrolix](https://hydrolix.io).

This repo is the landing place for Hydrolix-provided public skills. Some skills support Insights and other solutions bundles using the [Hydrolix MCP server](https://github.com/hydrolix/mcp-hydrolix); others can support broader Hydrolix workflows over time.

## Skills

| Skill | Description |
|-------|-------------|
| [bot-insights](skills/bot-insights/) | Bot traffic intelligence — scoring, verified/unverified classification, attack data analysis |
| [cdn-insights](skills/cdn-insights/) | Multi-CDN traffic analysis — cache efficiency, origin health, error rates, geographic distribution |
| [hydrolix-query-debugging](skills/hydrolix-query-debugging/) | Debug slow, timing-out, OOM, or circuit-broken Hydrolix SQL queries — enable `hdx_query_debug`, read `hdx.active_queries`, map errors to fixes |
| [zuplo-api-insights](skills/zuplo-api-insights/) | Zuplo API gateway analysis — auth, rate limiting, consumers, route performance, and edge security correlation |

## Prerequisites

Current bundled skills assume:

- A Hydrolix cluster with the relevant deployment or solution enabled
- The [Hydrolix MCP server](https://github.com/hydrolix/mcp-hydrolix) connected to your AI assistant

## Download

Standalone skill zip files are published by CI to the repo's GitHub Pages site and are also attached to releases when a release is cut.

## Installation

### Claude Code (plugin)

```bash
claude plugin install hydrolix/public-skills
```

This installs all skills at once. Skills are invoked as:

```
/public-skills:cdn-insights
/public-skills:bot-insights
/public-skills:zuplo-api-insights
```

To test locally before publishing:

```bash
claude --plugin-dir /path/to/public-skills
```

### Manual (any platform)

Download a skill zip from the GitHub Pages site or a release asset, then extract it into your platform's skills directory:

**Claude Code:**

```bash
unzip cdn-insights.zip -d ~/.claude/skills/
```

**OpenAI Codex:**

```bash
unzip cdn-insights.zip -d .agents/skills/
```

**Gemini CLI:**

```bash
unzip cdn-insights.zip -d .gemini/skills/
```

## Refreshing Schemas

The `references/` files in each skill are generated from bundle definitions. To regenerate:

```bash
uv run python scripts/generate-schema.py \
  --bundles-dir /path/to/solution-bundles/bundles \
  --schemas-dir /path/to/catalog-content-live-dashboard-validation/bundles \
  --output-dir skills
```

## Contributor Workflows

Ralph Loop plans and workflow templates are user-level artifacts maintained
outside this public skills repository. Use `~/src/plans` for plan packages and
`~/src/ralph-skills` for Ralph workflow guidance and templates.

## License

Apache 2.0 — see [LICENSE](LICENSE).
