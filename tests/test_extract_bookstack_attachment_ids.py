"""Unit tests for :func:`src.services.thirdparty_migrations._extract_bsexport.extract_bookstack_attachment_ids`."""

from src.services.thirdparty_migrations._extract_bsexport import (
    extract_bookstack_attachment_ids,
)


def test_plain_image_placeholder() -> None:
    assert extract_bookstack_attachment_ids("see [[bsexport:image:67]]") == [67]


def test_plain_attachment_placeholder() -> None:
    assert extract_bookstack_attachment_ids("dl: [[bsexport:attachment:42]]") == [42]


def test_html2text_escaped_placeholder() -> None:
    """`html2text` escapes brackets in image-src attributes."""
    body = "![x](\\[\\\[bsexport:image:67\\]\\])"
    assert extract_bookstack_attachment_ids(body) == [67]


def test_mixed_forms_are_deduplicated() -> None:
    body = "[[bsexport:image:67]] and ![](\\[\\\[bsexport:image:67\\]\\])"
    assert extract_bookstack_attachment_ids(body) == [67]


def test_other_kinds_are_ignored() -> None:
    """page / chapter / book kinds are not attachments in this project."""
    body = (
        "[[bsexport:page:1]] [[bsexport:chapter:2]] "
        "[[bsexport:book:3]] [[bsexport:image:5]]"
    )
    assert extract_bookstack_attachment_ids(body) == [5]


def test_keeps_insertion_order() -> None:
    body = "[[bsexport:image:9]] [[bsexport:attachment:2]] [[bsexport:image:9]]"
    assert extract_bookstack_attachment_ids(body) == [9, 2]


def test_unrelated_content_returns_empty() -> None:
    assert extract_bookstack_attachment_ids("match := re.FindStringSubmatch(\"key=123\")") == []
    assert extract_bookstack_attachment_ids("https://site.tld/api/attachments/abc-123") == []
    assert extract_bookstack_attachment_ids("plain key=att-a and key=att-b") == []
    assert extract_bookstack_attachment_ids("") == []
    assert extract_bookstack_attachment_ids("   \n\t  ") == []