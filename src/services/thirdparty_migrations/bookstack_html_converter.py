"""Convert BookStack HTML / Markdown to the project's markdown flavour.

The converter wraps :mod:`html2text` for HTML -> Markdown and adds
two rewrites that BookStack exports need before the resulting note
content is usable:

1. **Image refs.**  BookStack stores images as flat filenames under
   ``files/`` and references them as ``<img src="filename">`` in HTML
   or ``![alt](filename)`` in Markdown.  After we have uploaded each
   file via :class:`~src.services.attachments.AttachmentFacade`, we
   get back a fresh attachment key.  This module rewrites both the
   HTML and the Markdown forms so the references point at the new
   attachment URL produced by :func:`build_attachment_url`.

2. **BookStack cross-refs.**  BookStack pages may contain
   ``[[bsexport:type:id]]`` placeholders for cross-page / image
   references.  These are rewritten when the importer has collected a
   full source-id -> new-key map; otherwise they are left as literal
   text so the import does not lose content.

The two rewrites are split into separate methods so the import flow
can pass first-pass notes (no id map yet) to :meth:`convert_content`
and then re-run :meth:`rewrite_cross_references` once every page has
been inserted and every old id has a known target.
"""

from __future__ import annotations

import html2text
from typing import Callable, Dict, Iterable, Optional

from .bookstack_models import BookstackBook, BookstackPage

AttachmentUrlBuilder = Callable[[str], str]
"""Callable that turns an attachment key into a displayable URL."""


# Match both HTML and Markdown image refs that target a known filename.
# - HTML:  <img src="filename" ...>
# - MD:    ![alt](filename)
# The regex captures the filename so the caller can rewrite it.
_IMG_HTML_RE = __import__("re").compile(
    r'(?P<full><img\s[^>]*?src=")(?P<filename>[^"]+?)("[^>]*>)',
    __import__("re").IGNORECASE,
)
_IMG_MD_RE = __import__("re").compile(
    r"(?P<full>!\[[^\]]*\]\()(?P<filename>[^)\s]+)(\))",
)

# Cross-references: [[bsexport:type:id]]
_BSEXPORT_RE = __import__("re").compile(
    r"\[\[bsexport:(?P<kind>image|attachment|page|chapter|book):(?P<id>\d+)\]\]"
)


class BookstackHtmlConverter:
    """HTML / Markdown converter for the BookStack importer.

    Args:
        attachment_url_builder: turns an attachment key into the URL
            that should replace an ``files/<filename>`` reference.
        bodywidth: forwarded to :func:`html2text.html2text`.  ``0``
            disables line wrapping (we want the resulting Markdown to
            match the project's "no auto-wrap" style).
    """

    def __init__(
        self,
        attachment_url_builder: AttachmentUrlBuilder,
        *,
        bodywidth: int = 0,
    ) -> None:
        self._url_builder = attachment_url_builder
        self._bodywidth = bodywidth

    def html_to_markdown(self, html: str) -> str:
        """Convert `html` to Markdown.

        Empty / falsy input returns an empty string rather than
        html2text's default ``"\n"`` so callers can fall through
        cleanly.
        """
        if not html:
            return ""
        converter = html2text.HTML2Text()
        converter.bodywidth = self._bodywidth
        converter.ignore_links = False
        converter.ignore_images = False
        return converter.handle(html).strip()

    def convert_content(
        self,
        page: BookstackPage,
        file_index: Dict[str, str],
    ) -> str:
        """Pick the page's content source and run image-src rewrites.

        BookStack exports typically carry a ``markdown`` field that
        is non-empty for pages edited via the WYSIWYG; when empty
        we fall back to converting ``html``.  Both branches then go
        through :meth:`rewrite_image_sources` so the result references
        attachment URLs the importer will create.

        Args:
            page: the page whose content to convert.
            file_index: mapping of original ``files/<filename>`` ->
                the new attachment key (or any string the configured
                URL builder accepts).  Filenames not present in this
                map are left untouched.

        Returns:
            str: the converted markdown body.
        """
        if page.markdown and page.markdown.strip():
            body = page.markdown
        elif page.html:
            body = self.html_to_markdown(page.html)
        else:
            return ""
        return self.rewrite_image_sources(body, file_index)

    def rewrite_image_sources(
        self,
        content: str,
        file_index: Dict[str, str],
    ) -> str:
        """Replace ``files/<filename>`` references with attachment URLs.

        Operates on both ``<img src="filename">`` (HTML) and
        ``![alt](filename)`` (Markdown) forms.  Filenames not in
        `file_index` are left alone -- this typically happens for
        BookStack link-only attachments (no file to fetch) or for
        pages where the inline image was added via the gallery but
        not declared in the page payload.
        """
        def html_sub(match: __import__("re").Match[str]) -> str:
            filename = match.group("filename")
            new_key = self._lookup(file_index, filename)
            if new_key is None:
                return match.group(0)
            return f'{match.group("full")}{self._url_builder(new_key)}{match.group(3)}'

        def md_sub(match: __import__("re").Match[str]) -> str:
            filename = match.group("filename")
            new_key = self._lookup(file_index, filename)
            if new_key is None:
                return match.group(0)
            return f'{match.group("full")}{self._url_builder(new_key)}{match.group(3)}'

        content = _IMG_HTML_RE.sub(html_sub, content)
        content = _IMG_MD_RE.sub(md_sub, content)
        return content

    def rewrite_cross_references(
        self,
        content: str,
        id_index: Dict[str, Dict[int, str]],
        attachment_url_builder: Optional[AttachmentUrlBuilder] = None,
    ) -> str:
        """Rewrite ``[[bsexport:type:id]]`` cross-refs to attachment URLs.

        `id_index` maps the cross-ref kind (``"image"`` /
        ``"attachment"`` / ``"page"`` / ...) to a mapping of source id
        -> new attachment key.  For image / attachment kinds the
        value is fed through the URL builder (or the one configured
        on this instance); for the page / chapter / book kinds the
        value is treated as a note / directory id and dropped -- the
        importer does not preserve those relationships today, so the
        cross-ref is stripped.

        Kinds or ids missing from `id_index` are left as the literal
        ``[[bsexport:...]]`` text so the import does not silently
        drop content.
        """
        url_builder = attachment_url_builder or self._url_builder

        def sub(match: __import__("re").Match[str]) -> str:
            kind = match.group("kind")
            try:
                source_id = int(match.group("id"))
            except ValueError:
                return match.group(0)
            kind_map = id_index.get(kind)
            if not kind_map or source_id not in kind_map:
                return match.group(0)
            target = kind_map[source_id]
            if kind in ("image", "attachment"):
                return url_builder(target)
            return ""

        return _BSEXPORT_RE.sub(sub, content)

    @staticmethod
    def _lookup(file_index: Dict[str, str], filename: str) -> Optional[str]:
        # Tolerate an absolute-looking path inside the zip; we only
        # ever index by the basename-under-files/ form.
        if filename in file_index:
            return file_index[filename]
        basename = filename.rsplit("/", 1)[-1]
        return file_index.get(basename)


__all__ = ["BookstackHtmlConverter", "AttachmentUrlBuilder"]