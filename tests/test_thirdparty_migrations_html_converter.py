"""Unit tests for :class:`src.services.thirdparty_migrations.bookstack_html_converter.BookstackHtmlConverter`."""

from __future__ import annotations

from src.services.thirdparty_migrations.bookstack_html_converter import (
    BookstackHtmlConverter,
)
from src.services.thirdparty_migrations.bookstack_models import BookstackPage


def _builder(key: str) -> str:
    """Trivial URL builder that wraps the key in a marker URL."""
    return f"/u/{key}"


def _converter() -> BookstackHtmlConverter:
    return BookstackHtmlConverter(attachment_url_builder=_builder)


# ---- html_to_markdown ---------------------------------------------------


def test_empty_html_returns_empty_string() -> None:
    assert _converter().html_to_markdown("") == ""


def test_paragraph_round_trip() -> None:
    md = _converter().html_to_markdown("<p>hello world</p>")
    assert "hello world" in md


def test_table_round_trip() -> None:
    html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    md = _converter().html_to_markdown(html)
    # html2text emits pipe tables for tabular content.
    assert "A" in md and "B" in md and "1" in md and "2" in md


# ---- rewrite_image_sources ---------------------------------------------


def test_rewrites_html_img_src() -> None:
    body = '<p>before <img src="a.png" alt="x"/> after</p>'
    out = _converter().rewrite_image_sources(body, {"a.png": "key-A"})
    assert 'src="/u/key-A"' in out
    assert "a.png" not in out


def test_rewrites_markdown_image() -> None:
    body = "before ![alt](a.png) after"
    out = _converter().rewrite_image_sources(body, {"a.png": "key-A"})
    assert "![alt](/u/key-A)" in out


def test_unmapped_filename_is_left_alone_html() -> None:
    body = '<img src="missing.png"/>'
    out = _converter().rewrite_image_sources(body, {"other.png": "key-O"})
    assert out == body


def test_unmapped_filename_is_left_alone_markdown() -> None:
    body = "![alt](missing.png)"
    out = _converter().rewrite_image_sources(body, {})
    assert out == body


def test_lookup_falls_back_to_basename() -> None:
    body = '<img src="subdir/a.png"/>'
    out = _converter().rewrite_image_sources(body, {"a.png": "key-A"})
    assert 'src="/u/key-A"' in out


def test_multiple_images_in_one_body() -> None:
    body = (
        '<p><img src="a.png"/><img src="b.png"/></p>'
        ' ![one](a.png) ![two](b.png)'
    )
    out = _converter().rewrite_image_sources(
        body, {"a.png": "key-A", "b.png": "key-B"}
    )
    assert out.count("/u/key-A") == 2
    assert out.count("/u/key-B") == 2


# ---- convert_content ----------------------------------------------------


def test_convert_content_prefers_markdown_when_present() -> None:
    page = BookstackPage(
        id=1,
        name="P",
        markdown="original",
        html="<p>html</p>",
    )
    out = _converter().convert_content(page, {})
    assert out == "original"


def test_convert_content_falls_back_to_html() -> None:
    page = BookstackPage(id=1, name="P", markdown="", html="<p>only html</p>")
    out = _converter().convert_content(page, {})
    assert "only html" in out


def test_convert_content_rewrites_image_refs() -> None:
    page = BookstackPage(
        id=1,
        name="P",
        markdown='![caption](pic.png)',
        html="",
    )
    out = _converter().convert_content(page, {"pic.png": "key-P"})
    assert "![caption](/u/key-P)" in out


def test_convert_content_returns_empty_when_both_blank() -> None:
    page = BookstackPage(id=1, name="P")
    assert _converter().convert_content(page, {}) == ""


# ---- rewrite_cross_references ------------------------------------------


def test_rewrite_image_cross_ref() -> None:
    body = "see [[bsexport:image:42]] for details"
    out = _converter().rewrite_cross_references(
        body,
        {"image": {42: "key-42"}},
    )
    assert out == "see /u/key-42 for details"


def test_rewrite_attachment_cross_ref() -> None:
    """`[[bsexport:attachment:N]]` becomes a markdown link so the
    renderer shows a clickable link instead of a bare URL."""
    body = "report: [[bsexport:attachment:7]]"
    out = _converter().rewrite_cross_references(
        body,
        {"attachment": {7: "key-7"}},
        attachment_meta={7: "report.xml"},
    )
    assert out == "report: [report.xml](/u/key-7)"


def test_unknown_kind_is_left_alone() -> None:
    body = "see [[bsexport:page:99]] for the related page"
    out = _converter().rewrite_cross_references(body, {})
    assert out == body


def test_unknown_id_is_left_alone() -> None:
    body = "see [[bsexport:image:42]] for details"
    out = _converter().rewrite_cross_references(
        body,
        {"image": {1: "key-1"}},  # 42 not in map
    )
    assert out == body


def test_multiple_cross_refs_in_one_body() -> None:
    body = "[[bsexport:image:1]] and [[bsexport:image:2]]"
    out = _converter().rewrite_cross_references(
        body,
        {"image": {1: "key-1", 2: "key-2"}},
    )
    assert out == "/u/key-1 and /u/key-2"


# ---- inline [[bsexport:...]] image src (BookStack gallery forms) ----


def test_rewrites_inline_bsexport_image_src_html() -> None:
    """`<img src="[[bsexport:image:N]]">` resolves via bsexport_index."""
    body = '<p><img src="[[bsexport:image:67]]" alt="x"/></p>'
    out = _converter().rewrite_image_sources(
        body, {}, bsexport_index={67: "key-67"}
    )
    assert 'src="/u/key-67"' in out
    assert "[[bsexport" not in out


def test_rewrites_inline_bsexport_image_src_html2text_escaped() -> None:
    """html2text escapes brackets in image src attributes."""
    body = r"![x](\[\[bsexport:image:67\]\])"
    out = _converter().rewrite_image_sources(
        body, {}, bsexport_index={67: "key-67"}
    )
    assert "![x](/u/key-67)" in out


def test_inline_bsexport_unknown_id_is_left_alone() -> None:
    body = '<img src="[[bsexport:image:404]]" alt="x"/>'
    out = _converter().rewrite_image_sources(
        body, {}, bsexport_index={1: "key-1"}
    )
    assert out == body


def test_rewrite_cross_references_handles_escaped_form() -> None:
    """html2text-escaped `[[bsexport:image:N]]` is rewritten too."""
    body = r"see \[\[bsexport:image:67\]\] for details"
    out = _converter().rewrite_cross_references(
        body, {"image": {67: "key-67"}}
    )
    assert out == "see /u/key-67 for details"


def test_rewrite_cross_references_partial_escaping() -> None:
    """Mixed plain + escaped cross-refs in one body."""
    body = r"a [[bsexport:image:1]] b \[\[bsexport:image:2]\] c"
    out = _converter().rewrite_cross_references(
        body, {"image": {1: "key-1", 2: "key-2"}}
    )
    assert out == "a /u/key-1 b /u/key-2 c"


# Pinned regression: previously the converter always emitted the
# inline image URL, which made the renderer try to embed a broken
# icon when the file was an XML / JSON / DOCX attachment.  After
# the change, every non-image / non-PDF ref becomes a clickable
# ``[filename](url)`` link.


def _converter_with_distinct_link_builder() -> BookstackHtmlConverter:
    """A converter that uses a different URL for the link builder."""
    return BookstackHtmlConverter(
        attachment_url_builder=lambda k: f"/img/{k}",
        attachment_link_builder=lambda k: f"/dl/{k}",
    )


def test_non_image_inline_html_ref_becomes_link() -> None:
    body = '<p>before <img src="data.xml" alt="x"/> after</p>'
    out = _converter_with_distinct_link_builder().rewrite_image_sources(
        body, {"data.xml": "key-X"}
    )
    assert "[data.xml](/dl/key-X)" in out
    assert "/img/key-X" not in out


def test_non_image_inline_markdown_ref_becomes_link() -> None:
    body = "before ![caption](data.json) after"
    out = _converter_with_distinct_link_builder().rewrite_image_sources(
        body, {"data.json": "key-J"}
    )
    assert "[data.json](/dl/key-J)" in out
    assert "![caption]" not in out


def test_pdf_inline_ref_stays_as_inline_image() -> None:
    """PDFs are kept as inline images because the renderer can embed them."""
    body = '<p>see <img src="report.pdf" alt="r"/></p>'
    out = _converter_with_distinct_link_builder().rewrite_image_sources(
        body, {"report.pdf": "key-P"}
    )
    assert 'src="/img/key-P"' in out
    assert "/dl/" not in out


def test_image_inline_ref_stays_as_inline_image() -> None:
    body = '<p>see <img src="photo.png" alt="r"/></p>'
    out = _converter_with_distinct_link_builder().rewrite_image_sources(
        body, {"photo.png": "key-I"}
    )
    assert 'src="/img/key-I"' in out
    assert "/dl/" not in out


def test_mixed_image_and_non_image_refs() -> None:
    body = (
        '<p><img src="photo.png"/><img src="data.xml"/></p>'
        ' ![one](photo.png) ![two](data.json)'
    )
    out = _converter_with_distinct_link_builder().rewrite_image_sources(
        body, {"photo.png": "key-I", "data.xml": "key-X", "data.json": "key-J"}
    )
    # Images use the image URL.
    assert out.count("/img/key-I") == 2
    # Non-images use the link URL wrapped in a markdown link.
    assert "[data.xml](/dl/key-X)" in out
    assert "[data.json](/dl/key-J)" in out


def test_default_link_builder_falls_back_to_image_builder() -> None:
    """When `attachment_link_builder` is not set, the image builder
    is reused for the link URL.  This keeps older callers working
    without changes to the wire format they pin in tests."""
    body = "see ![caption](data.xml)"
    out = _converter().rewrite_image_sources(body, {"data.xml": "key-X"})
    assert "[data.xml](/u/key-X)" in out


def test_non_image_attachment_cross_ref_becomes_link() -> None:
    body = "report: [[bsexport:attachment:7]]"
    out = _converter_with_distinct_link_builder().rewrite_cross_references(
        body,
        {"attachment": {7: "key-7"}},
        attachment_meta={7: "report.xml"},
    )
    assert out == "report: [report.xml](/dl/key-7)"


def test_image_cross_ref_still_uses_image_url() -> None:
    body = "see [[bsexport:image:42]] for details"
    out = _converter_with_distinct_link_builder().rewrite_cross_references(
        body, {"image": {42: "key-42"}}
    )
    assert out == "see /img/key-42 for details"


def test_attachment_cross_ref_without_meta_uses_placeholder() -> None:
    body = "report: [[bsexport:attachment:7]]"
    out = _converter_with_distinct_link_builder().rewrite_cross_references(
        body,
        {"attachment": {7: "key-7"}},
    )
    # The placeholder is the source id so the link still renders
    # even when the caller forgot to pass attachment_meta.
    assert out == "report: [attachment-7](/dl/key-7)"