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

## Download

Each skill is available as a standalone zip file from the [latest release](https://github.com/hydrolix/insight-skills/releases/tag/v1.0.0):

| Skill | Download |
|-------|----------|
| bot-insights | [bot-insights.zip](https://github.com/hydrolix/insight-skills/releases/download/v1.0.0/bot-insights.zip) |
| cdn-insights | [cdn-insights.zip](https://github.com/hydrolix/insight-skills/releases/download/v1.0.0/cdn-insights.zip) |

## Installation

### Claude Code (plugin)

```bash
claude plugin install hydrolix/insight-skills
```

This installs all skills at once. Skills are invoked as:

```
/insight-skills:cdn-insights
/insight-skills:bot-insights
```

To test locally before publishing:

```bash
claude --plugin-dir /path/to/insight-skills
```

### Manual (any platform)

Download a skill zip from the table above, then extract it into your platform's skills directory:

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
python scripts/generate-schema.py \
  --bundles-dir /path/to/solution-bundles/bundles \
  --schemas-dir /path/to/catalog-content-live-dashboard-validation/bundles \
  --output-dir skills
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
