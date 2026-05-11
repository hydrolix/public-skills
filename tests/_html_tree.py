"""Tiny stdlib-only HTML tree helper for parity tests.

Generation in this project uses Jinja2; parity testing in M2/M3 needs a
queryable tree representation of rendered HTML so assertions can read
"every percentage under the Movement table keyed by (row, column)" rather
than dumping raw strings against snapshots. Standalone ``html.parser`` is
event-based, which would force every assertion to track open-tag state by
hand. This module wraps it in a minimal tree-builder so tests can speak
in tree terms while staying inside the standard library — no third-party
dependency.

Scope: enough to support the parity invariants spelled out in plan v3's
M2.1 (heading set + order, section presence by class, keyed table rows,
warning lines, file-size envelope). It is **not** a full DOM
implementation:

- Void elements collapse into self-closing nodes; their ``children`` is
  empty.
- ``<style>``/``<script>`` content is captured verbatim as text on the
  node but is not tokenized.
- HTML entities decode to their literal value (``&amp;`` → ``&``) so
  numeric/text assertions match the on-page reading, not the raw markup.

Nothing here is intended to render HTML — only to read it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable, Iterator

# HTML5 void elements — no closing tag, no children.
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


@dataclass
class Node:
    """A single element in the parsed tree.

    ``tag`` is lowercased; ``attrs`` is a flat dict of attribute names to
    their string values (the last-seen value wins on duplicates, mirroring
    browser behavior). ``children`` interleaves child Nodes; raw text
    between or inside children is kept on the parent's ``texts`` list in
    document order. ``classes`` is pre-split for fast class-based queries.
    """

    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    parent: "Node | None" = None

    @property
    def classes(self) -> frozenset[str]:
        cls = self.attrs.get("class") or ""
        return frozenset(cls.split())

    def has_class(self, name: str) -> bool:
        return name in self.classes

    def text(self, separator: str = " ") -> str:
        """All text in this subtree, document order, joined by ``separator``.

        Whitespace is **not** normalized — callers that want collapsed
        whitespace should ``re.sub(r"\\s+", " ", ...)`` the result. Keeps
        the separator visible so multi-line content stays distinguishable.
        """
        parts: list[str] = []
        # Interleave parent's own text with descendants in document order.
        # The tree-builder records ``texts`` and ``children`` separately,
        # but we approximate document order by yielding all text from the
        # subtree.
        for piece in _walk_text(self):
            if piece.strip():
                parts.append(piece)
        return separator.join(parts)

    def find(
        self,
        tag: str | None = None,
        *,
        class_: str | None = None,
    ) -> "Node | None":
        for node in self.iter():
            if tag is not None and node.tag != tag:
                continue
            if class_ is not None and not node.has_class(class_):
                continue
            return node
        return None

    def find_all(
        self,
        tag: str | None = None,
        *,
        class_: str | None = None,
    ) -> list["Node"]:
        result: list[Node] = []
        for node in self.iter():
            if tag is not None and node.tag != tag:
                continue
            if class_ is not None and not node.has_class(class_):
                continue
            result.append(node)
        return result

    def iter(self) -> Iterator["Node"]:
        """Depth-first preorder traversal starting at ``self``."""
        yield self
        for child in self.children:
            yield from child.iter()


def _walk_text(node: Node) -> Iterable[str]:
    """Approximate document-order text from a subtree.

    Strict document order would require interleaving each parent's
    ``texts`` items with the boundaries of its children. For parity
    assertions we only need "all visible text under this subtree", which
    is what this walker yields — text from the node itself first, then
    text from each child in order.
    """
    for piece in node.texts:
        yield piece
    for child in node.children:
        yield from _walk_text(child)


class _TreeBuilder(HTMLParser):
    """Internal HTMLParser subclass that materializes a tree of ``Node``s."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node(tag="#document")
        self._stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: (v if v is not None else "") for k, v in attrs}
        node = Node(tag=tag, attrs=attr_dict, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        if tag in _VOID_TAGS:
            return
        self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<foo />` self-closing — always treated as void, regardless of
        # whether the spec recognizes the tag as void.
        attr_dict = {k: (v if v is not None else "") for k, v in attrs}
        node = Node(tag=tag, attrs=attr_dict, parent=self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        # Pop until we find a matching start tag. Silently tolerate
        # mismatched closes — the test corpus is well-formed enough that
        # this is mainly a defense against `<br>` vs `<br/>` style
        # noise. If no match exists, ignore the close.
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self._stack[-1].texts.append(data)


def parse(html: str) -> Node:
    """Parse a complete HTML document into a ``Node`` tree.

    The returned node has tag ``"#document"``; its children include the
    ``<html>`` element (when present) plus any pre-body text. Callers
    that care about the body root should call ``parse(html).find("body")``.
    """
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


# ---------------------------------------------------------------------------
# Convenience helpers used by the parity assertions.
# ---------------------------------------------------------------------------


def heading_sequence(root: Node, levels: Iterable[str] = ("h1", "h2", "h3")) -> list[
    tuple[str, str]
]:
    """Document-order list of ``(tag, text)`` headings.

    Used for the "heading set + order" parity invariant. Whitespace in
    each heading's text is collapsed to single spaces so trivial
    indentation differences between renders don't break the assertion.
    """
    import re

    headings: list[tuple[str, str]] = []
    levels = tuple(levels)
    for node in root.iter():
        if node.tag in levels:
            text = re.sub(r"\s+", " ", node.text()).strip()
            headings.append((node.tag, text))
    return headings


def class_set(root: Node) -> set[str]:
    """Every class name referenced anywhere in the tree.

    Used to feed the parity allowlist: any class names that differ
    between legacy and engine renders must either disappear or appear
    in the allowlist with a justification.
    """
    classes: set[str] = set()
    for node in root.iter():
        classes.update(node.classes)
    return classes


def warning_comments(html: str) -> list[str]:
    """Stderr-style WARNING lines extracted from the rendered comments.

    Both legacy and engine paths emit warnings via the CLI on stderr, not
    inside the HTML. For parity assertion purposes the parity harness
    captures stderr separately and passes the list in — this helper is
    here so callers can normalize the format consistently.
    """
    return [line.strip() for line in html.splitlines() if line.strip().startswith("WARNING:")]
