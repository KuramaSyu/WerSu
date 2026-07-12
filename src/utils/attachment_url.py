from __future__ import annotations

import mimetypes
from urllib.parse import quote


DEFAULT_BASE = "/api/attachments/image"
DEFAULT_LINK_BASE = "/api/attachments/"
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


def build_attachment_link_url(
    key: str,
    *,
    base: str = DEFAULT_LINK_BASE,
) -> str:
    """Build a ``/api/attachments/?key=...`` URL for non-image / non-PDF files.

    The link URL is the shape used for general file attachments: the
    renderer treats it as a clickable link rather than an inline
    image.  The key is URL-encoded once (not twice like the image
    URL) because the route looks the key up verbatim.

    Args:
        key: the attachment key returned by
            :class:`~src.services.attachments.AttachmentFacade.post_attachment`.
        base: the URL path before the query string.

    Returns:
        str: the full URL with the key single-URL-encoded.
    """
    once = quote(key, safe="")
    return f"{base}?key={once}"


def is_image_or_pdf_attachment(filename: str) -> bool:
    """Return True when `filename` is an image or a PDF.

    Used by the BookStack importer to decide between the inline
    image URL (current behaviour) and the link URL (new for files
    the renderer cannot preview).  PDFs are treated as embeddable
    because most browsers render them inline; everything else falls
    through to the link format.
    """
    ctype, _ = mimetypes.guess_type(filename)
    if not ctype:
        return False
    return ctype.startswith("image/") or ctype == "application/pdf"


__all__ = [
    "build_attachment_url",
    "build_attachment_link_url",
    "is_image_or_pdf_attachment",
    "DEFAULT_BASE",
    "DEFAULT_LINK_BASE",
    "DEFAULT_WIDTH",
    "DEFAULT_FORMAT",
]