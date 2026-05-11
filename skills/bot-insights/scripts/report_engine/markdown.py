"""CommonMark → safe-HTML pipeline for analyst_notes prose.

LLMs (or human analysts) hand us Markdown via wrapper analyst_notes. We render
to HTML with a tight allowlist so the renderer can never inject scripts or
attributes via prose. Templates own the page title — top-level `<h1>` from
note bodies is demoted to `<h2>`.
"""

from __future__ import annotations

from typing import Any

# Heavy deps (bleach, markdown_it, markupsafe) are imported lazily inside
# render_safe() so md_escape — which has no third-party dependencies — can
# be unit-tested on bare pytest installations without an opt-in.
_MD: Any = None
_Markup: Any = None
_bleach: Any = None


def _ensure_render_deps() -> None:
    """Import the Markdown-rendering deps on first use. Cached after."""
    global _MD, _Markup, _bleach
    if _MD is not None:
        return
    from markupsafe import Markup as _M
    import bleach as _b
    from markdown_it import MarkdownIt

    _MD = MarkdownIt(
        "commonmark", {"html": False, "linkify": False, "typographer": False}
    )
    _Markup = _M
    _bleach = _b

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


def render_safe(text: str) -> Any:
    """Render Markdown to a sanitized HTML fragment.

    Returns ``markupsafe.Markup`` so Jinja2 leaves the fragment alone.
    Heavy deps (bleach, markdown_it) load lazily — analyst-note prose
    rendering is the only caller, and tests on a bare interpreter never
    reach this code.
    """
    _ensure_render_deps()
    if not text:
        return _Markup("")
    rendered = _MD.render(text)
    # Demote any top-level h1 — the report template owns the page title.
    rendered = rendered.replace("<h1>", "<h2>").replace("</h1>", "</h2>")
    cleaned = _bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    return _Markup(cleaned)


# Characters that have syntactic meaning in CommonMark inline contexts and in
# GitHub-flavored Markdown table rows. Escaping them keeps producer-supplied
# identifiers (metric names, entity strings, target descriptors) from
# accidentally bolding/italicizing/breaking-out-of-table-cells in the rendered
# Markdown. Pipe is the table-cell separator; backslash needs escaping first to
# avoid recursive escaping; the rest are inline metacharacters.
_MD_ESCAPE_CHARS = "\\`*_{}[]()#+-.!|<>&"


def md_escape(text: object) -> str:
    """Escape a value so it can interpolate safely into a Markdown document.

    Used at every identifier/label interpolation site in ``.md.j2``
    templates so user/producer-supplied strings don't change document
    structure. ``None`` becomes the empty string; non-strings are
    coerced via ``str()`` first.

    Escapes the GFM-table cell separator (``|``), the CommonMark
    emphasis markers (``*``, ``_``), backticks, square brackets, and
    the other punctuation that CommonMark treats as syntactically
    meaningful at the start of a line or inside an inline context. The
    backslash itself is escaped first to avoid recursive escaping when
    the loop processes ``\\\\``.
    """
    if text is None:
        return ""
    s = str(text)
    if not s:
        return ""
    # Backslash first; otherwise later iterations would double-escape the
    # backslashes we just inserted.
    out = []
    for ch in s:
        if ch in _MD_ESCAPE_CHARS:
            out.append("\\")
        out.append(ch)
    return "".join(out)
