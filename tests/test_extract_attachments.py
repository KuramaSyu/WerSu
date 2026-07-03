"""Unit tests for :func:`src.utils.extract_attachments.extract_attachment_ids`."""

from src.utils.extract_attachments import extract_attachment_ids


_REAL_URL = (
    "/api/attachments/image?width=720&format=webp"
    "&key=attachments%252F019f1481-a466-7438-8fdd-ffa4913012b4"
)
_REAL_KEY = "attachments/019f1481-a466-7438-8fdd-ffa4913012b4"


def test_url_path_yields_id() -> None:
    assert extract_attachment_ids("https://site.tld/api/attachments/abc-123") == ["abc-123"]


def test_multiple_urls_in_order_with_dedup() -> None:
    content = (
        "https://x.tld/api/attachments/aaa-111 "
        "https://y.tld/api/attachments/bbb-222 "
        "https://z.tld/api/attachments/aaa-111"
    )
    assert extract_attachment_ids(content) == ["aaa-111", "bbb-222"]


def test_jwt_query_param_is_stripped() -> None:
    assert extract_attachment_ids(
        "https://x.tld/api/attachments/with-jwt?jwt=abc.def.ghi"
    ) == ["with-jwt"]


def test_url_slug_plus_key_prefers_decoded_key() -> None:
    """When a URL has both a path slug and a `key=` query, the key wins."""
    assert extract_attachment_ids(_REAL_URL) == [_REAL_KEY]


def test_key_form_bare_quoted_and_spaced() -> None:
    content = 'plain key=att-a and key="att-b" and key = "att-c"'
    assert extract_attachment_ids(content) == ["att-a", "att-b", "att-c"]


def test_key_form_preserves_attachments_prefix_after_decoding() -> None:
    """`attachments/`, `attachments%2F`, and `attachments%252F` all keep the prefix."""
    for prefix in ("attachments/", "attachments%2F", "attachments%252F"):
        assert extract_attachment_ids(f"key={prefix}{_REAL_KEY.removeprefix('attachments/')}") == [_REAL_KEY]


def test_unrelated_and_empty_content() -> None:
    unrelated = (
        "home https://example.com/ "
        "image https://cdn.example.com/img.png "
        "nested /api/users/42 monkey=att-x"
    )
    assert extract_attachment_ids(unrelated) == []
    assert extract_attachment_ids("") == []
    assert extract_attachment_ids("   \n\t  ") == []