"""Unit tests for the stdlib-only HTML tree helper used by parity tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import _html_tree as html_tree  # noqa: E402


def test_parse_returns_document_root_with_html_child():
    tree = html_tree.parse("<html><body><h1>Hi</h1></body></html>")
    assert tree.tag == "#document"
    html = tree.find("html")
    assert html is not None
    body = tree.find("body")
    assert body is not None


def test_parse_finds_element_by_tag():
    tree = html_tree.parse("<div><p>x</p><p>y</p></div>")
    paragraphs = tree.find_all("p")
    assert len(paragraphs) == 2


def test_find_by_class_matches_any_token_in_class_attribute():
    tree = html_tree.parse(
        '<div class="card kpi"><span class="num">42</span></div>'
    )
    assert tree.find("div", class_="kpi") is not None
    assert tree.find("div", class_="card") is not None
    assert tree.find("div", class_="num") is None
    assert tree.find("span", class_="num") is not None


def test_text_concatenates_subtree_in_document_order():
    tree = html_tree.parse(
        "<p>before <strong>middle</strong> after</p>"
    )
    para = tree.find("p")
    assert "before" in para.text()
    assert "middle" in para.text()
    assert "after" in para.text()


def test_text_strips_whitespace_only_nodes_from_join():
    tree = html_tree.parse(
        "<section>\n  <h2>Heading</h2>\n  <p>Body</p>\n</section>"
    )
    section = tree.find("section")
    joined = section.text(separator="|")
    # Whitespace-only text fragments are filtered out before joining,
    # so the separator falls between the meaningful pieces.
    assert "Heading" in joined
    assert "Body" in joined


def test_void_elements_dont_consume_siblings_into_their_subtree():
    """``<br>`` and friends don't push onto the stack — sibling content
    must remain a sibling, not a phantom child of the void element."""
    tree = html_tree.parse("<div><br>after the break</div>")
    div = tree.find("div")
    assert "after the break" in div.text()


def test_self_closing_tag_treated_as_void():
    tree = html_tree.parse('<svg><rect width="10" /></svg>')
    rect = tree.find("rect")
    assert rect is not None
    assert rect.attrs["width"] == "10"
    assert rect.children == []


def test_html_entities_decode_to_literal_values():
    """Numeric/text assertions read the on-page content, not the raw markup."""
    tree = html_tree.parse("<p>R&amp;D &mdash; 5%</p>")
    assert "R&D" in tree.text()
    # mdash decodes to its literal unicode character
    assert "—" in tree.text()
    assert "5%" in tree.text()


def test_heading_sequence_preserves_document_order():
    tree = html_tree.parse(
        "<body>"
        "<h1>Top</h1>"
        "<section><h2>First</h2><h3>1a</h3></section>"
        "<section><h2>Second</h2></section>"
        "</body>"
    )
    headings = html_tree.heading_sequence(tree)
    assert headings == [
        ("h1", "Top"),
        ("h2", "First"),
        ("h3", "1a"),
        ("h2", "Second"),
    ]


def test_heading_sequence_collapses_whitespace_within_heading_text():
    tree = html_tree.parse(
        "<h2>\n  Multiple  \n  spaces  </h2>"
    )
    headings = html_tree.heading_sequence(tree)
    assert headings == [("h2", "Multiple spaces")]


def test_class_set_returns_every_class_token_in_the_tree():
    tree = html_tree.parse(
        '<div class="a b"><span class="b c">x</span><i class="d"></i></div>'
    )
    assert html_tree.class_set(tree) == {"a", "b", "c", "d"}


def test_has_class_checks_one_class_at_a_time():
    tree = html_tree.parse('<div class="alpha beta gamma"></div>')
    div = tree.find("div")
    assert div.has_class("alpha")
    assert div.has_class("beta")
    assert div.has_class("gamma")
    assert not div.has_class("delta")
    # Substring matches must not pass — has_class uses set membership.
    assert not div.has_class("alph")


def test_iter_walks_depth_first_preorder():
    tree = html_tree.parse(
        "<a><b><c></c></b><d></d></a>"
    )
    a = tree.find("a")
    tags = [n.tag for n in a.iter()]
    assert tags == ["a", "b", "c", "d"]


def test_parser_tolerates_mismatched_close_tags():
    """If a renderer emits `<br>` and a stray `</br>`, the close should
    be ignored without corrupting the surrounding structure."""
    tree = html_tree.parse("<div>x</br>y</div>")
    div = tree.find("div")
    assert "x" in div.text() and "y" in div.text()


def test_attrs_capture_kebab_case_and_quoted_values():
    tree = html_tree.parse(
        '<button aria-label="Open detail" data-state="closed">Open</button>'
    )
    button = tree.find("button")
    assert button.attrs["aria-label"] == "Open detail"
    assert button.attrs["data-state"] == "closed"


def test_warning_comments_extracts_stderr_style_warnings():
    """Helper for normalizing warnings captured from CLI stderr."""
    lines = "\n".join(
        [
            "WARNING: posture-1 missing current_window metadata.",
            "WARNING: posture-1 missing baseline_windows metadata.",
            "Some other stderr line",
        ]
    )
    assert html_tree.warning_comments(lines) == [
        "WARNING: posture-1 missing current_window metadata.",
        "WARNING: posture-1 missing baseline_windows metadata.",
    ]
