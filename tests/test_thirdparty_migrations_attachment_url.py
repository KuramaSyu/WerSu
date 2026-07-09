"""Unit tests for :func:`src.services.thirdparty_migrations._attachment_url.build_attachment_url`.

The URL format produced by the helper must be compatible with
:func:`src.utils.extract_attachments.extract_attachment_ids`, since
the BookStack importer rewrites every inline image ref to this shape
and the note service later scans note content for attachment ids in
exactly the same way.  If the format drifts the imported notes will
lose their attachment linkage.
"""

from __future__ import annotations

from src.services.thirdparty_migrations._attachment_url import (
    DEFAULT_BASE,
    DEFAULT_FORMAT,
    DEFAULT_WIDTH,
    build_attachment_url,
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