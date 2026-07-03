from __future__ import annotations

import re
from typing import List
from urllib.parse import unquote


# Capture group `id` accepts every supported reference format:
# 1. URL path:   /api/attachments/<id>
# 2. Query form: key=<id>,  key = "<id>",  "key":"<id>"  (with optional
#    surrounding whitespace and double-quotes; the lookbehind
#    `(?<![A-Za-z0-9_-])` prevents matches inside longer identifiers
#    such as `monkey=` or `key-id=`).
# The `key=` value may be URL-encoded one or two levels deep
# (`%2F` or `%252F`); both decode to a single `/` so the prefix is
# preserved verbatim.  The `attachments/` prefix is kept -- callers
# pass the captured value straight through to the S3 lookup, which
# expects the prefixed key.
_ATTACHMENT_URL_RE = re.compile(
    r"/api/attachments/(?P<id>[A-Za-z0-9_\-]+)"
    r"|(?<![A-Za-z0-9_\-])key\s*=\s*\"?(?P<key>[A-Za-z0-9_\-%/]+)\"?",
    re.IGNORECASE,
)


def _decode_key(raw: str) -> str:
    """URL-decode once or twice (handles double-encoded `%252F` -> `/`)."""
    return unquote(unquote(raw))


def extract_attachment_ids(content: str) -> List[str]:
    """Extract attachment ids from URLs with following formats:
        https://some-site.tld/api/attachments/<uuid>
        http://example.test/api/attachments/<uuid>?jwt=<token>
        /api/attachments/<slug>?...&key=attachments%252F<uuid>...  # this one is currently used

    Args:
        content: raw note body (may be markdown or plain text).

    Returns:
        List[str]: unique attachment ids, in the order they first appear.
    """
    if not content:
        return []

    seen: set[str] = set()
    result: List[str] = []
    path_superseded: set[int] = set()

    # First pass: find every key= reference and mark the path slug in
    # its enclosing URL as superseded, so the second pass can skip it.
    for match in _ATTACHMENT_URL_RE.finditer(content):
        if match.group("id") is not None:
            continue
        url_start = _find_url_start(content, match.start())
        path_match = _ATTACHMENT_URL_RE.search(
            content,
            url_start,
            match.start(),
        )
        if path_match is not None and path_match.group("id") is not None:
            path_superseded.add(path_match.start())

    # Second pass: emit ids.
    for match in _ATTACHMENT_URL_RE.finditer(content):
        path_id = match.group("id")
        if path_id is not None:
            if match.start() in path_superseded:
                continue
            raw = path_id
        else:
            raw = _decode_key(match.group("key"))
        if raw in seen:
            continue
        seen.add(raw)
        result.append(raw)
    return result


def _find_url_start(content: str, pos: int) -> int:
    """Return the index where the URL containing `pos` starts.

    Searches backward for ``http://``, ``https://``, or ``/api/`` and
    falls back to the start of `content` when none is found.
    """
    for marker in ("https://", "http://", "/api/"):
        idx = content.rfind(marker, 0, pos)
        if idx != -1:
            return idx
    return 0


__all__ = ["extract_attachment_ids"]