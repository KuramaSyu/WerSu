"""Unit tests for :func:`src.utils.attachment_url.build_attachment_url` and
:func:`src.utils.attachment_url.build_attachment_link_url`.

The image URL format produced by the helper must be compatible with
:func:`src.utils.extract_attachments.extract_attachment_ids`, since
the BookStack importer rewrites every inline image ref to this shape
and the note service later scans note content for attachment ids in
exactly the same way.  If the format drifts the imported notes will
lose their attachment linkage.

The link URL format is the new shape used for non-image / non-PDF
attachments (e.g. XML, JSON, DOCX).  The BookStack importer wraps
the key in a ``[filename](url)`` markdown link so the renderer
shows a clickable link rather than trying to embed a non-image
file inline.
"""

from __future__ import annotations

from src.utils.attachment_url import (
    DEFAULT_BASE,
    DEFAULT_FORMAT,
    DEFAULT_LINK_BASE,
    DEFAULT_WIDTH,
    build_attachment_link_url,
    build_attachment_url,
    is_image_or_pdf_attachment,
)
from src.utils.extract_attachments import extract_attachment_ids


_REAL_KEY = "attachments/019f1481-a466-7438-8fdd-ffa4913012b4"


def test_default_shape() -> None:
    url = build_attachment_url(_REAL_KEY)
    assert url.startswith(DEFAULT_BASE + "?")
    assert f"width={DEFAULT_WIDTH}" in url
    assert f"format={DEFAULT_FORMAT}" in url
    assert "key=" in url


def test_key_is_double_url_encoded() -> None:
    url = build_attachment_url(_REAL_KEY)
    # `/` should appear as %252F (double-encoded); the inner /
    # survives one round of decoding only.
    assert "%252F" in url
    # extract_attachment_ids must round-trip back to the bare key.
    assert extract_attachment_ids(url) == [_REAL_KEY]


def test_extract_attachment_ids_finds_inline_image() -> None:
    url = build_attachment_url(_REAL_KEY)
    note_body = f"here is an inline image: ![diagram]({url})"
    assert extract_attachment_ids(note_body) == [_REAL_KEY]


def test_custom_base_width_format() -> None:
    url = build_attachment_url(
        _REAL_KEY, base="/custom/path", width=1200, fmt="png"
    )
    assert url.startswith("/custom/path?")
    assert "width=1200" in url
    assert "format=png" in url
    assert extract_attachment_ids(url) == [_REAL_KEY]


def test_idempotent_across_builders() -> None:
    """Two builders with the same args should produce the same URL."""
    a = build_attachment_url(_REAL_KEY)
    b = build_attachment_url(_REAL_KEY)
    assert a == b


def test_works_for_bare_uuid_keys() -> None:
    """Some attachment keys don't have an `attachments/` prefix."""
    bare = "019f1481-a466-7438-8fdd-ffa4913012b4"
    assert extract_attachment_ids(build_attachment_url(bare)) == [bare]


def test_link_url_default_shape() -> None:
    url = build_attachment_link_url(_REAL_KEY)
    assert url.startswith(DEFAULT_LINK_BASE + "?")
    assert "key=" in url
    # Link URLs do not carry width / format (the renderer treats
    # them as plain file downloads, not image transformations).
    assert "width=" not in url
    assert "format=" not in url


def test_link_url_uses_single_encoding() -> None:
    url = build_attachment_link_url(_REAL_KEY)
    # The link URL is single-encoded (`%2F`), unlike the image URL
    # which is double-encoded (`%252F`).
    assert "%2F" in url
    assert "%252F" not in url
    # extract_attachment_ids must still round-trip back to the bare
    # key -- otherwise the attachment linking pass would lose the
    # XML/JSON files the importer references via the new format.
    assert extract_attachment_ids(url) == [_REAL_KEY]


def test_link_url_in_markdown_link_round_trips() -> None:
    url = build_attachment_link_url(_REAL_KEY)
    note_body = f"download the [report.xml]({url})"
    assert extract_attachment_ids(note_body) == [_REAL_KEY]


def test_link_url_custom_base() -> None:
    url = build_attachment_link_url(_REAL_KEY, base="/custom/path")
    assert url.startswith("/custom/path?")
    assert "key=" in url
    assert extract_attachment_ids(url) == [_REAL_KEY]


def test_link_url_idempotent_across_builders() -> None:
    a = build_attachment_link_url(_REAL_KEY)
    b = build_attachment_link_url(_REAL_KEY)
    assert a == b


def test_link_url_works_for_bare_uuid_keys() -> None:
    bare = "019f1481-a466-7438-8fdd-ffa4913012b4"
    assert extract_attachment_ids(build_attachment_link_url(bare)) == [bare]


def test_is_image_or_pdf_recognises_images() -> None:
    assert is_image_or_pdf_attachment("photo.png")
    assert is_image_or_pdf_attachment("photo.PNG")
    assert is_image_or_pdf_attachment("photo.jpg")
    assert is_image_or_pdf_attachment("photo.jpeg")
    assert is_image_or_pdf_attachment("photo.gif")
    assert is_image_or_pdf_attachment("photo.webp")
    assert is_image_or_pdf_attachment("photo.svg")


def test_is_image_or_pdf_recognises_pdfs() -> None:
    assert is_image_or_pdf_attachment("report.pdf")


def test_is_image_or_pdf_rejects_other_types() -> None:
    assert not is_image_or_pdf_attachment("data.xml")
    assert not is_image_or_pdf_attachment("data.json")
    assert not is_image_or_pdf_attachment("doc.docx")
    assert not is_image_or_pdf_attachment("archive.zip")
    assert not is_image_or_pdf_attachment("notes.txt")


def test_is_image_or_pdf_returns_false_for_unknown_extension() -> None:
    """Filenames without a recognisable extension fall through to link format."""
    assert not is_image_or_pdf_attachment("README")
    assert not is_image_or_pdf_attachment("file-without-ext")