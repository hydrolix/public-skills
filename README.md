# Hydrolix Insight Skills

Skills for analyzing data in [Hydrolix](https://hydrolix.io) solutions bundle deployments using the [Hydrolix MCP server](https://github.com/hydrolix/mcp-hydrolix).

Each skill is an analytical playbook for a specific solutions bundle — it teaches AI assistants what tables and columns exist, what questions to ask, how to write effective queries, and what pitfalls to avoid.

## Skills

| Skill | Description |
|-------|-------------|
| [bot-insights](skills/bot-insights/) | Bot traffic intelligence — scoring, verified/unverified classification, attack data analysis |
| [cdn-insights](skills/cdn-insights/) | Multi-CDN traffic analysis — cache efficiency, origin health, error rates, geographic distribution |

## Prerequisites

- A Hydrolix cluster with a solutions bundle deployed
- The [Hydrolix MCP server](https://github.com/hydrolix/mcp-hydrolix) connected to your AI assistant

## Installation

### Claude Code (plugin)

```bash
claude plugin install hydrolix/insight-skills
```

This installs all skills at once. Skills are invoked as:

```
/insight-skills:cdn-insights
/insight-skills:bot-insights
/insight-skills:zuplo-api-insights
```

To test locally before publishing:

```bash
claude --plugin-dir /path/to/insight-skills
```

### Claude Code (manual)

Copy or symlink individual skill directories:

```bash
# Project-level
cp -r skills/cdn-insights .claude/skills/cdn-insights

# User-level
cp -r skills/cdn-insights ~/.claude/skills/cdn-insights
```

### OpenAI Codex

```bash
cp -r skills/cdn-insights .agents/skills/cdn-insights
```

### Gemini CLI

```bash
cp -r skills/cdn-insights .gemini/skills/cdn-insights
```

## Refreshing Schemas

The `references/` files in each skill are generated from bundle definitions. To regenerate:

```bash
python scripts/generate-schema.py \
  --bundles-dir /path/to/solution-bundles/bundles \
  --schemas-dir /path/to/catalog-content-live-dashboard-validation/bundles \
  --output-dir skills
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
