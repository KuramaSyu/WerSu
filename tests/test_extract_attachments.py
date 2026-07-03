"""Unit tests for :func:`src.utils.extract_attachments.extract_attachment_ids`."""

from tests.stubs.user_context import _UserContext as UserContext
from src.utils.extract_attachments import extract_attachment_ids


def test_extracts_single_attachment_url() -> None:
    """A single URL yields one id."""
    content = "see [img](https://site.tld/api/attachments/abc-123) for context"
    assert extract_attachment_ids(content) == ["abc-123"]


def test_extracts_multiple_urls_in_order() -> None:
    """Multiple URLs are returned in first-appearance order."""
    content = (
        "![a](https://x.tld/api/attachments/aaa-111) "
        "and ![b](http://y.tld/api/attachments/bbb-222) "
        "and ![c](https://z.tld/api/attachments/ccc-333)"
    )
    assert extract_attachment_ids(content) == ["aaa-111", "bbb-222", "ccc-333"]


def test_deduplicates_repeated_ids() -> None:
    """A id mentioned twice is returned once, on its first occurrence."""
    content = (
        "first https://x.tld/api/attachments/aaa-111 "
        "second https://y.tld/api/attachments/aaa-111"
    )
    assert extract_attachment_ids(content) == ["aaa-111"]


def test_strips_optional_jwt_query_param() -> None:
    """The optional `?jwt=...` query parameter does not leak into the id."""
    content = "https://x.tld/api/attachments/with-jwt?jwt=abc.def.ghi"
    assert extract_attachment_ids(content) == ["with-jwt"]


def test_strips_trailing_path_segments() -> None:
    """Anything after the id (e.g. a slash) is not part of the id."""
    content = "https://x.tld/api/attachments/with-slash/extra/path"
    assert extract_attachment_ids(content) == ["with-slash"]


def test_handles_uuid_shaped_ids() -> None:
    """Realistic UUIDv7-shaped ids are captured."""
    content = "https://site.tld/api/attachments/018f3e9a-7c1d-7abc-9def-0123456789ab"
    assert extract_attachment_ids(content) == ["018f3e9a-7c1d-7abc-9def-0123456789ab"]


def test_ignores_unrelated_urls() -> None:
    """URLs that do not match `/api/attachments/<id>` are ignored."""
    content = (
        "home https://example.com/ "
        "image https://cdn.example.com/img.png "
        "nested /api/users/42"
    )
    assert extract_attachment_ids(content) == []


def test_returns_empty_for_empty_content() -> None:
    """Empty or whitespace-only content yields no ids."""
    assert extract_attachment_ids("") == []
    assert extract_attachment_ids("   \n\t  ") == []