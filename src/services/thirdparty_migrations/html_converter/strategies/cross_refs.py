"""Rewrite ``[[bsexport:type:id]]`` cross-refs to attachment URLs.

BookStack pages may contain ``[[bsexport:type:id]]`` placeholders
for cross-page / image / attachment references.  These are
rewritten when the importer has collected a full source-id ->
new-key map; otherwise they are left as literal text so the
import does not lose content.

This is a pure markdown post-processor.  The HTML pass does not
need to do anything here -- :mod:`html2text` always escapes the
brackets, so the cross-ref ends up in the markdown either as
``[[bsexport:type:id]]`` or as ``\\[\\[bsexport:type:id]\\]``,
both of which the regexes below handle.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, Optional

from src.utils.attachment_url import is_image_or_pdf_attachment

from ..context import ConverterContext
from ..strategy import MarkdownPostprocessorStrategy


# Cross-references: ``[[bsexport:type:id]]`` in plain prose.
_BSEXPORT_RE = re.compile(
    r"\[\[bsexport:(?P<kind>image|attachment|page|chapter|book):(?P<id>\d+)\]\]"
)

# Same as above but for the backslash-escaped form that
# :mod:`html2text` emits when the original HTML carried brackets
# in an attribute value (e.g. an image src).  Without this the
# rewrite would leave ``\\[\\[bsexport:...\\]\\]`` text in place
# and the renderer would try to fetch a bogus key.
_BSEXPORT_ESCAPED_RE = re.compile(
    r"\\?\["            # optional opening backslash
    r"\\?\["            # optional second opening backslash
    r"bsexport:"
    r"(?P<kind>image|attachment|page|chapter|book)"
    r":(?P<id>\d+)"
    r"\\?\]"            # optional closing backslash
    r"\\?\]"            # optional second closing backslash
)


class CrossRefStrategy(MarkdownPostprocessorStrategy):
    """Rewrite ``[[bsexport:type:id]]`` cross-refs to attachment URLs.

    Args:
        url_builder: turns an attachment key into the inline image
            URL used for image kind cross-refs.  When `None`
            (the default) the strategy is a no-op so a converter
            built without a URL builder does not crash.
        link_builder: turns an attachment key into the URL used
            for ``attachment`` kind cross-refs.  Defaults to
            ``url_builder`` so older callers and tests that only
            configure the image URL still work.
        id_index: maps the cross-ref kind (``"image"`` /
            ``"attachment"`` / ``"page"`` / ...) to a mapping of
            source id -> new attachment key.  When empty (the
            default) the strategy is a no-op so an early
            conversion pass before the full id_index is known
            does not crash.
        attachment_meta: optional source id -> original filename
            map for ``attachment`` kind cross-refs.  Used as the
            link text when an attachment is rendered as a
            markdown link instead of an inline image.
    """

    def __init__(
        self,
        url_builder: Optional[Callable[[str], str]] = None,
        *,
        link_builder: Optional[Callable[[str], str]] = None,
        id_index: Optional[Dict[str, Dict[int, str]]] = None,
        attachment_meta: Optional[Dict[int, str]] = None,
    ) -> None:
        self._url_builder = url_builder
        self._link_builder = link_builder or url_builder
        self._id_index = id_index or {}
        self._attachment_meta = attachment_meta

    @property
    def name(self) -> str:
        return "cross_refs"

    def postprocess_markdown(
        self, markdown: str, context: ConverterContext
    ) -> str:
        if not self._id_index or self._url_builder is None:
            return markdown

        def replace(match: re.Match[str]) -> str:
            return self._render(match)

        markdown = _BSEXPORT_RE.sub(replace, markdown)
        markdown = _BSEXPORT_ESCAPED_RE.sub(replace, markdown)
        return markdown

    def _render(self, match: re.Match[str]) -> str:
        kind = match.group("kind")
        try:
            source_id = int(match.group("id"))
        except ValueError:
            return match.group(0)
        kind_map = self._id_index.get(kind)
        if not kind_map or source_id not in kind_map:
            return match.group(0)
        target = kind_map[source_id]
        if kind == "image":
            return self._url_builder(target)  # type: ignore[misc]
        if kind == "attachment":
            filename = _resolve_attachment_filename(
                kind, source_id, self._attachment_meta
            )
            # image/PDF filenames keep the inline image URL
            # through ``link_builder``; every other file type
            # is rendered as a markdown link so the renderer
            # does not try to embed something it cannot display.
            if is_image_or_pdf_attachment(filename):
                return self._url_builder(target)  # type: ignore[misc]
            return f"[{filename}]({self._link_builder(target)})"  # type: ignore[misc]
        return ""


def _resolve_attachment_filename(
    kind: str,
    source_id: int,
    attachment_meta: Optional[Dict[int, str]],
) -> str:
    """Return the filename to use as a markdown link text for a cross-ref.

    `kind` is the cross-ref kind (``"image"`` or ``"attachment"``).
    For ``"image"`` we have no filename in `attachment_meta` and
    just return a stable placeholder so the link still renders;
    for ``"attachment"`` we look up the original filename from
    `attachment_meta` and fall back to a generic placeholder when
    the caller did not pass any metadata.
    """
    if attachment_meta is not None and source_id in attachment_meta:
        return attachment_meta[source_id]
    if kind == "image":
        return f"image-{source_id}"
    return f"attachment-{source_id}"


__all__ = ["CrossRefStrategy"]