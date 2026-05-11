# Hydrolix AI Toolkit

AI assistant skills built by [Hydrolix](https://hydrolix.io).

This repo is the landing place for Hydrolix-provided skills. Some skills support Insights and other solutions bundles using the [Hydrolix MCP server](https://github.com/hydrolix/mcp-hydrolix); others can support broader Hydrolix workflows over time.

## Skills

| Skill | Description |
|-------|-------------|
| [bot-insights](skills/bot-insights/) | Bot traffic intelligence — scoring, verified/unverified classification, attack data analysis |
| [debugging-hydrolix-queries](skills/debugging-hydrolix-queries/) | Use when a Hydrolix query is timing out, OOMing, returning DB::Exception or HdxStorageError, hitting a circuit breaker, or running slower than expected over MCP, the HTTP Query API, or another SQL client |

## Prerequisites

Current bundled skills assume:

- A Hydrolix cluster with the relevant deployment or solution enabled
- The [Hydrolix MCP server](https://github.com/hydrolix/mcp-hydrolix) connected to your AI assistant

## Download

Standalone skill zip files are published by CI to the repo's GitHub Pages site and are also attached to releases when a release is cut.

## Installation

### Claude Code (plugin)

```bash
claude plugin install hydrolix/hydrolix-ai-toolkit
```

This installs all skills at once. Skills are invoked as:

```
/hydrolix:bot-insights
/hydrolix:debugging-hydrolix-queries
```

To test locally before publishing:

```bash
claude --plugin-dir /path/to/hydrolix-ai-toolkit
```

### Manual (any platform)

Download a skill zip from the GitHub Pages site or a release asset, then extract it into your platform's skills directory:

**Claude Code:**

```bash
unzip bot-insights.zip -d ~/.claude/skills/
```

**OpenAI Codex:**

```bash
unzip bot-insights.zip -d .agents/skills/
```

**Gemini CLI:**

```bash
unzip bot-insights.zip -d .gemini/skills/
```

## Refreshing Schemas

The `references/` files in each skill are generated from bundle definitions. To regenerate:

```bash
uv run python scripts/generate-schema.py \
  --bundles-dir /path/to/solution-bundles/bundles \
  --schemas-dir /path/to/catalog-content-live-dashboard-validation/bundles \
  --output-dir skills
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
