"""Rewrite ``files/<filename>`` references to attachment URLs.

BookStack stores images as flat filenames under ``files/`` and
references them as ``<img src="filename">`` in HTML or
``![alt](filename)`` in Markdown.  After the importer has
uploaded each file via
:class:`~src.services.attachment_facade.AttachmentFacadeImpl`, it
gets back a fresh attachment key.  This strategy rewrites both
the HTML and the Markdown forms so the references point at the
new attachment URL produced by
:func:`~src.utils.attachment_url.build_attachment_url`.

This is a pure markdown post-processor.  The HTML pass does not
need to do anything here because :mod:`html2text` already turns
``<img src="filename">`` into ``![alt](filename)`` for inline
images; the only case where the source HTML carries a literal
``<img>`` tag is when the attribute value already pointed at a
``[[bsexport:...]]`` placeholder, which the inline-bsexport
regexes below handle in the markdown pass.

The file_index is supplied by the orchestrator (it is built
from the upload pass that runs before any conversion) and stashed
on the strategy instance.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, Optional

from src.utils.attachment_url import is_image_or_pdf_attachment

from ..context import ConverterContext
from ..strategy import HtmlPreprocessorStrategy, MarkdownPostprocessorStrategy


# Match both HTML and Markdown image refs that target a known
# filename.  The regex captures the filename so the caller can
# rewrite it.
_IMG_HTML_RE = re.compile(
    r'(?P<full><img\s[^>]*?src=")(?P<filename>[^"]+?)("[^>]*>)',
    re.IGNORECASE,
)
_IMG_MD_RE = re.compile(
    r"(?P<full>!\[[^\]]*\]\()(?P<filename>[^)\s]+)(\))",
)


# BookStack also writes inline image refs that point at a
# cross-ref rather than a filename, e.g.
# `<img src="[[bsexport:image:67]]" ...>` in raw HTML or
# `![x](\[\[bsexport:image:67\]\])` after html2text has escaped
# the brackets.  We accept both forms and rewrite them when the
# `bsexport_index` carried in the context maps the source id to
# a new attachment key.
_PLACEHOLDER_BODY = (
    r"\\?\["            # optional opening backslash (html2text-escaped)
    r"\\?\["            # optional second opening backslash
    r"bsexport:"
    r"(?P<kind>image|attachment)"
    r":(?P<id>\d+)"
    r"\\?\]"            # optional closing backslash
    r"\\?\]"            # optional second closing backslash
)

_IMG_BSEXPORT_HTML_RE = re.compile(
    r'(?P<full><img\s[^>]*?src=")'
    + _PLACEHOLDER_BODY
    + r'("[^>]*>)',
    re.IGNORECASE,
)
_IMG_BSEXPORT_MD_RE = re.compile(
    r"(?P<full>!\[[^\]]*\]\()"
    + _PLACEHOLDER_BODY
    + r"(\))",
)


class ImageRefStrategy(MarkdownPostprocessorStrategy):
    """Rewrite ``files/<filename>`` references to attachment URLs.

    Args:
        url_builder: turns an attachment key into the inline image
            URL used for image / PDF refs.
        link_builder: turns an attachment key into the URL used
            by the markdown link rendered for non-image / non-PDF
            refs.  Defaults to ``url_builder`` so older callers
            and tests that only configure the image URL still
            work.
        file_index: optional mapping of original
            ``files/<filename>`` -> the new attachment key the
            orchestrator produced.  When `None` (the default) the
            strategy is a no-op so a converter built without a
            populated file_index does not crash.
        bsexport_index: optional source id -> new attachment key
            map for inline ``[[bsexport:image:N]]`` /
            ``[[bsexport:attachment:N]]`` cross-refs that point
            at a known attachment.
        attachment_meta: optional source id -> original filename
            map for ``attachment`` kind cross-refs.  Used as the
            link text when an attachment is rendered as a
            markdown link instead of an inline image.
    """

    def __init__(
        self,
        url_builder: Callable[[str], str],
        *,
        link_builder: Optional[Callable[[str], str]] = None,
        file_index: Optional[Dict[str, str]] = None,
        bsexport_index: Optional[Dict[int, str]] = None,
        attachment_meta: Optional[Dict[int, str]] = None,
    ) -> None:
        self._url_builder = url_builder
        self._link_builder = link_builder or url_builder
        self._file_index = file_index or {}
        self._bsexport_index = bsexport_index or {}
        self._attachment_meta = attachment_meta

    @property
    def name(self) -> str:
        return "image_refs"

    def postprocess_markdown(
        self, markdown: str, context: ConverterContext
    ) -> str:
        if self._file_index:
            markdown = _IMG_HTML_RE.sub(self._html_sub(), markdown)
            markdown = _IMG_MD_RE.sub(self._md_sub(), markdown)
        if self._bsexport_index:
            markdown = _IMG_BSEXPORT_HTML_RE.sub(self._bsexport_sub("html"), markdown)
            markdown = _IMG_BSEXPORT_MD_RE.sub(self._bsexport_sub("md"), markdown)
        return markdown

    def _html_sub(self):
        file_index = self._file_index
        url_builder = self._url_builder
        link_builder = self._link_builder

        def replace(match: re.Match[str]) -> str:
            filename = match.group("filename")
            new_key = _lookup(file_index, filename)
            if new_key is None:
                return match.group(0)
            if is_image_or_pdf_attachment(filename):
                return f'{match.group("full")}{url_builder(new_key)}{match.group(3)}'
            return f"[{filename}]({link_builder(new_key)})"

        return replace

    def _md_sub(self):
        file_index = self._file_index
        url_builder = self._url_builder
        link_builder = self._link_builder

        def replace(match: re.Match[str]) -> str:
            filename = match.group("filename")
            new_key = _lookup(file_index, filename)
            if new_key is None:
                return match.group(0)
            if is_image_or_pdf_attachment(filename):
                return f'{match.group("full")}{url_builder(new_key)}{match.group(3)}'
            return f"[{filename}]({link_builder(new_key)})"

        return replace

    def _bsexport_sub(self, kind: str):
        """Build a substitution callback for ``[[bsexport:kind:N]]`` refs.

        `kind` is ``"html"`` or ``"md"`` -- it controls which
        trailing group of the regex match we keep (the
        closing ``>`` of the HTML ``<img>`` tag vs the closing
        ``)`` of the markdown ``![alt](...)`` form).
        """
        url_builder = self._url_builder
        link_builder = self._link_builder
        bsexport_index = self._bsexport_index
        attachment_meta = self._attachment_meta

        def replace(match: re.Match[str]) -> str:
            try:
                source_id = int(match.group("id"))
            except ValueError:
                return match.group(0)
            new_key = bsexport_index.get(source_id)
            if new_key is None:
                return match.group(0)
            trailing = match.group(4)  # closing ">" or ")"
            ref_kind = match.group("kind")
            if ref_kind == "image":
                replacement = url_builder(new_key)
                return f'{match.group("full")}{replacement}{trailing}'
            filename = _resolve_attachment_filename(
                ref_kind, source_id, attachment_meta
            )
            return f"[{filename}]({link_builder(new_key)})"

        return replace


def _lookup(file_index: Dict[str, str], filename: str) -> Optional[str]:
    """Tolerate an absolute-looking path inside the zip; we only
    ever index by the basename-under-files/ form."""
    if filename in file_index:
        return file_index[filename]
    basename = filename.rsplit("/", 1)[-1]
    return file_index.get(basename)


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


__all__ = ["ImageRefStrategy"]