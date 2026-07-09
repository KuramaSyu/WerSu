"""Build the canonical attachment URL the frontend uses for images.

The frontend renders every note/directory image through a single route:

    /api/attachments/image?width=720&format=webp&key=<urlencoded key>

The key is the S3 object key returned by
:class:`src.services.attachments.AttachmentFacade.post_attachment`,
typically ``attachments/<uuid>``.  The query parameter is double-URL-
encoded so that :func:`src.utils.extract_attachments.extract_attachment_ids`
can decode it back to the bare key (the regex there handles one or two
rounds of decoding).  Keeping the same shape means everything that
already scans note content for attachment references keeps working
without any new parser code.
"""

from __future__ import annotations

from urllib.parse import quote


DEFAULT_BASE = "/api/attachments/image"
DEFAULT_WIDTH = 720
DEFAULT_FORMAT = "webp"


def build_attachment_url(
    key: str,
    *,
    base: str = DEFAULT_BASE,
    width: int = DEFAULT_WIDTH,
    fmt: str = DEFAULT_FORMAT,
) -> str:
    """Build a ``/api/attachments/image?...&key=...`` URL for `key`.

    Args:
        key: the attachment key returned by
            :class:`~src.services.attachments.AttachmentFacade.post_attachment`.
        base: the URL path before the query string.
        width: image width passed to the renderer.
        fmt: image format passed to the renderer.

    Returns:
        str: the full URL with the key double-URL-encoded.
    """
    once = quote(key, safe="")
    twice = quote(once, safe="")
    return f"{base}?width={width}&format={fmt}&key={twice}"


__all__ = ["build_attachment_url", "DEFAULT_BASE", "DEFAULT_WIDTH", "DEFAULT_FORMAT"]