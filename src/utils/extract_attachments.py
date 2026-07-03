from __future__ import annotations

import re
from typing import List


_ATTACHMENT_URL_RE = re.compile(
    r"/api/attachments/(?P<id>[A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)


def extract_attachment_ids(content: str) -> List[str]:
    """Return every attachment id found in `content`, preserving order.
    The content should contain URLs like these:
        https://some-site.tld/api/attachments/<uuid>
        http://example.test/api/attachments/<uuid>?jwt=<token>

    Args:
        content: raw note body (may be markdown or plain text).

    Returns:
        List[str]: unique attachment ids, in the order they first appear.
    """
    if not content:
        return []

    seen: set[str] = set()
    result: List[str] = []
    for match in _ATTACHMENT_URL_RE.finditer(content):
        attachment_id = match.group("id")
        if attachment_id in seen:
            continue
        seen.add(attachment_id)
        result.append(attachment_id)
    return result


__all__ = ["extract_attachment_ids"]