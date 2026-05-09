"""CommonMark → safe-HTML pipeline for analyst_notes prose.

LLMs (or human analysts) hand us Markdown via wrapper analyst_notes. We render
to HTML with a tight allowlist so the renderer can never inject scripts or
attributes via prose. Templates own the page title — top-level `<h1>` from
note bodies is demoted to `<h2>`.
"""

from __future__ import annotations

from markupsafe import Markup

# Importing at module scope so the optional dependency error surfaces on import.
import bleach
from markdown_it import MarkdownIt

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})

ALLOWED_TAGS = {
    "p", "br",
    "ul", "ol", "li",
    "strong", "em", "code", "pre",
    "h2", "h3", "h4",
    "blockquote",
    "a",
}

ALLOWED_ATTRS = {
    "a": ["href", "title"],
}

# bleach allows http(s) and mailto by default; restrict explicitly to be safe.
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def render_safe(text: str) -> Markup:
    """Render Markdown to a sanitized HTML fragment."""
    if not text:
        return Markup("")
    rendered = _MD.render(text)
    # Demote any top-level h1 — the report template owns the page title.
    rendered = rendered.replace("<h1>", "<h2>").replace("</h1>", "</h2>")
    cleaned = bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    return Markup(cleaned)
