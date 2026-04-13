#!/bin/bash
set -e

# Generates a GitHub Pages site with:
# - Landing page listing all skills with direct download links
# - Per-platform install instructions
# - JSON manifest for programmatic access
#
# Expected env vars (set by GitHub Actions):
#   GITHUB_REPOSITORY  - e.g., hydrolix/insight-skills
#   GITHUB_RUN_ID      - current workflow run
#   RELEASE_TAG        - git tag for the release (e.g., v1.0.0), empty if not a release

SITE_DIR="site"
mkdir -p "$SITE_DIR"

TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
REPO_URL="https://github.com/${GITHUB_REPOSITORY:-hydrolix/insight-skills}"
RELEASE_TAG="${RELEASE_TAG:-latest}"

# Build skill data
SKILL_CARDS=""
SKILL_JSON_ITEMS=""
FIRST=true

for skill_dir in skills/*/; do
    [ -f "${skill_dir}SKILL.md" ] || continue
    skill_name=$(basename "$skill_dir")

    # Extract description from YAML frontmatter (handles multi-line >)
    description=$(awk '
        /^---$/ { if (++c==2) exit }
        /^description:/ {
            sub(/^description: *>? */, "")
            if ($0 != "") { print; next }
            # multi-line: read continuation lines
            while ((getline line) > 0) {
                if (line !~ /^  /) break
                sub(/^  /, "", line)
                printf "%s ", line
            }
            print ""
            exit
        }
    ' "${skill_dir}SKILL.md" | head -1 | sed 's/  */ /g; s/ *$//')

    description_escaped=$(echo "$description" | sed 's/"/\&quot;/g')
    description_json=$(echo "$description" | sed 's/"/\\"/g')

    # Count files in references/
    ref_count=0
    if [ -d "${skill_dir}references" ]; then
        ref_count=$(find "${skill_dir}references" -type f | wc -l | tr -d ' ')
    fi

    # JSON
    if [ "$FIRST" = true ]; then
        FIRST=false
    else
        SKILL_JSON_ITEMS="$SKILL_JSON_ITEMS,"
    fi
    skill_page_url="$REPO_URL/tree/main/skills/$skill_name"
    download_path="./$skill_name.zip"
    SKILL_JSON_ITEMS="$SKILL_JSON_ITEMS
    {
      \"name\": \"$skill_name\",
      \"description\": \"$description_json\",
      \"filename\": \"$skill_name.zip\",
      \"downloadUrl\": \"$download_path\",
      \"referenceFiles\": $ref_count
    }"

    # HTML card
    SKILL_CARDS="$SKILL_CARDS
            <div class=\"skill-card\">
                <h2><a href=\"$skill_page_url\">$skill_name</a></h2>
                <p class=\"description\">$description_escaped</p>
                <div class=\"meta\">$ref_count reference file(s)</div>
                <a href=\"$download_path\" class=\"download-btn\">
                    Download $skill_name.zip
                </a>
            </div>"
done

# Generate HTML
cat > "$SITE_DIR/index.html" << 'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hydrolix Insight Skills</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inconsolata:wght@400;500;600&family=Lato:wght@400;700;900&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Lato', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #FDF5E8;
            color: #000000;
            min-height: 100vh;
        }

        .site-header {
            background: #FFFFFF;
            border-bottom: 1px solid #000000;
        }

        /* Nav */
        .nav {
            display: flex;
            align-items: center;
            justify-content: space-between;
            max-width: 1100px;
            margin: 0 auto;
            padding: 1rem 2rem;
        }

        .nav-logo {
            display: inline-flex;
            align-items: center;
            text-decoration: none;
        }

        .nav-logo img {
            display: block;
            width: 172px;
            height: auto;
        }

        .nav-links {
            display: flex;
            gap: 1.5rem;
            list-style: none;
        }

        .nav-links a {
            font-family: 'Inconsolata', monospace;
            font-weight: 500;
            font-size: 0.8rem;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            color: #000000;
            text-decoration: none;
            transition: color 0.2s;
        }

        .nav-links a:hover { color: #666666; }

        /* Hero */
        .hero {
            background: #0F0E17;
            color: #FFFFFE;
            padding: 4rem 2rem;
            text-align: center;
        }

        .hero-inner {
            max-width: 700px;
            margin: 0 auto;
        }

        .hero h1 {
            font-family: 'Lato', sans-serif;
            font-weight: 900;
            font-size: 2.6rem;
            line-height: 1.15;
            margin-bottom: 1rem;
        }

        .hero h1 span { color: #36BDB1; }

        .hero p {
            font-family: 'Lato', sans-serif;
            font-size: 1.05rem;
            color: rgba(255, 255, 254, 0.75);
            line-height: 1.7;
            margin-bottom: 1.5rem;
        }

        .hero a { color: #36BDB1; }
        .hero a:hover { color: #FFC614; }

        /* Content */
        .container {
            max-width: 1100px;
            margin: 0 auto;
            padding: 3rem 2rem;
        }

        /* Install section */
        .install-section {
            background: #FFFFFF;
            border: 1px solid #000000;
            border-radius: 0;
            padding: 1.5rem;
            margin-bottom: 2.5rem;
        }

        .install-section h3 {
            font-family: 'Inconsolata', monospace;
            font-weight: 500;
            font-size: 0.85rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: #000000;
            margin-bottom: 1rem;
        }

        .install-tabs {
            display: flex;
            gap: 0.5rem;
            margin-bottom: 1rem;
            flex-wrap: wrap;
        }

        .install-tabs button {
            font-family: 'Inconsolata', monospace;
            font-weight: 500;
            font-size: 0.8rem;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            background: #FFFFFF;
            color: #000000;
            border: 1px solid #000000;
            padding: 0.4rem 0.8rem;
            border-radius: 9999px;
            cursor: pointer;
            transition: all 0.2s;
        }

        .install-tabs button:hover {
            background: #FDF5E8;
        }

        .install-tabs button.active {
            background: #003366;
            color: #FFFFFF;
            border-color: #003366;
        }

        .install-content { display: none; }
        .install-content.active { display: block; }

        pre {
            background: #0F0E17;
            border: none;
            border-radius: 4px;
            padding: 0.8rem 1rem;
            overflow-x: auto;
            font-size: 0.85rem;
            line-height: 1.5;
        }

        code {
            font-family: 'Inconsolata', monospace;
            color: #36BDB1;
        }

        /* Skill cards */
        .skills-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .skill-card {
            background: #FFFFFF;
            border: 1px solid #000000;
            border-radius: 0;
            padding: 1.5rem;
        }

        .skill-card h2 {
            font-family: 'Lato', sans-serif;
            font-weight: 700;
            font-size: 1.15rem;
            margin-bottom: 0.5rem;
        }

        .skill-card h2 a {
            color: #003366;
            text-decoration: none;
        }

        .skill-card h2 a:hover { color: #000000; }

        .skill-card .description {
            font-family: 'Lato', sans-serif;
            color: #333333;
            font-size: 0.9rem;
            line-height: 1.6;
            margin-bottom: 0.75rem;
        }

        .skill-card .meta {
            font-family: 'Inconsolata', monospace;
            font-size: 0.75rem;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            color: #666666;
            margin-bottom: 1rem;
        }

        .download-btn {
            font-family: 'Inconsolata', monospace;
            font-weight: 500;
            font-size: 0.8rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            display: inline-block;
            background: #FFC614;
            color: #000000;
            border: 1px solid #000000;
            padding: 0.6rem 1.2rem;
            border-radius: 9999px;
            text-decoration: none;
            transition: background-color 0.2s, color 0.2s;
        }

        .download-btn:hover {
            background: #000000;
            color: #FFFFFF;
        }

        a { color: #36BDB1; text-decoration: none; }
        a:hover { color: #3D958D; }

        /* Footer */
        .footer {
            border-top: 1px solid #000000;
            color: #333333;
            padding: 2rem;
            text-align: center;
            font-size: 0.85rem;
        }

        .footer a {
            color: #000000;
            text-decoration: none;
        }

        .footer a:hover { color: #3D958D; }
    </style>
</head>
<body>
    <div class="site-header">
        <nav class="nav">
            <a href="https://hydrolix.io" class="nav-logo" aria-label="Hydrolix">
                <img src="https://hydrolix.io/wp-content/uploads/2023/10/Hydrolix-Logotype.svg" alt="Hydrolix">
            </a>
            <ul class="nav-links">
                <li><a href="https://github.com/hydrolix/mcp-hydrolix">MCP Server</a></li>
                <li><a href="https://docs.hydrolix.io">Docs</a></li>
            </ul>
        </nav>
    </div>

    <div class="hero">
        <div class="hero-inner">
            <h1>Insights <span>Skills</span></h1>
            <p>Each skill is a self-contained analytical playbook for a Hydrolix
            solutions bundle. Install one and your AI assistant knows what tables
            exist, what questions to ask, how to write the queries, and what
            pitfalls to avoid.</p>
        </div>
    </div>

    <div class="container">
        <div class="install-section">
            <h3>Install</h3>
            <div class="install-tabs">
                <button class="active" onclick="showTab('claude')">Claude Code (plugin)</button>
                <button onclick="showTab('manual')">Manual (any platform)</button>
                <button onclick="showTab('codex')">OpenAI Codex</button>
                <button onclick="showTab('gemini')">Gemini CLI</button>
            </div>
            <div id="tab-claude" class="install-content active">
                <pre><code>claude plugin install hydrolix/insight-skills</code></pre>
            </div>
            <div id="tab-manual" class="install-content">
                <pre><code># Download and extract a skill, then copy to your platform's skills directory
unzip cdn-insights.zip -d ~/.claude/skills/</code></pre>
            </div>
            <div id="tab-codex" class="install-content">
                <pre><code># Download and extract a skill
unzip cdn-insights.zip -d .agents/skills/</code></pre>
            </div>
            <div id="tab-gemini" class="install-content">
                <pre><code># Download and extract a skill
unzip cdn-insights.zip -d .gemini/skills/</code></pre>
            </div>
        </div>

HTMLEOF

# Inject skill cards
cat >> "$SITE_DIR/index.html" << CARDSEOF
        <div class="skills-grid">$SKILL_CARDS
        </div>

CARDSEOF

# Close HTML
cat >> "$SITE_DIR/index.html" << FOOTEREOF
    </div>

    <div class="footer">
        <p>Last updated: $TIMESTAMP</p>
        <p>
            <a href="$REPO_URL">GitHub</a> &middot;
            <a href="$REPO_URL/releases">Releases</a> &middot;
            <a href="https://hydrolix.io">hydrolix.io</a>
        </p>
    </div>

    <script>
        function showTab(name) {
            document.querySelectorAll('.install-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.install-tabs button').forEach(el => el.classList.remove('active'));
            document.getElementById('tab-' + name).classList.add('active');
            event.target.classList.add('active');
        }
    </script>
</body>
</html>
FOOTEREOF

# Generate JSON manifest
cat > "$SITE_DIR/skills.json" << JSONEOF
{
  "lastUpdated": "$TIMESTAMP",
  "repository": "$REPO_URL",
  "release": "$RELEASE_TAG",
  "downloadBase": ".",
  "skills": [$SKILL_JSON_ITEMS
  ]
}
JSONEOF

echo "Site generated:"
echo "  $SITE_DIR/index.html"
echo "  $SITE_DIR/skills.json"
