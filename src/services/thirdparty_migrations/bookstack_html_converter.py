"""Convert BookStack HTML / Markdown to the project's markdown flavour.

The converter wraps :mod:`html2text` for HTML -> Markdown and adds
two rewrites that BookStack exports need before the resulting note
content is usable:

1. **Image refs.**  BookStack stores images as flat filenames under
   ``files/`` and references them as ``<img src="filename">`` in HTML
   or ``![alt](filename)`` in Markdown.  After we have uploaded each
   file via :class:`~src.services.attachment_facade.AttachmentFacadeImpl`, we
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
from typing import Callable, Dict, Optional

from src.utils.attachment_url import is_image_or_pdf_attachment

from .bookstack_models import  BookstackPage

AttachmentUrlBuilder = Callable[[str], str]
"""Callable that turns an attachment key into a displayable URL."""


# Match both HTML and Markdown image refs that target a known filename.
# - HTML:  <img src="filename" ...>
# - MD:    ![alt](filename)
# The regex captures the <filename> so the caller can rewrite it.
_IMG_HTML_RE = __import__("re").compile(
    r'(?P<full><img\s[^>]*?src=")(?P<filename>[^"]+?)("[^>]*>)',
    __import__("re").IGNORECASE,
)
_IMG_MD_RE = __import__("re").compile(
    r"(?P<full>!\[[^\]]*\]\()(?P<filename>[^)\s]+)(\))",
)

# BookStack also writes inline image refs that point at a cross-ref
# rather than a filename, e.g. `<img src="[[bsexport:image:67]]" ...>`
# in raw HTML or `![x](\[\[bsexport:image:67\]\])` after :mod:`html2text`
# has escaped the brackets.  We accept both forms and rewrite them
# when :meth:`BookstackHtmlConverter.rewrite_image_sources` is given
# a `bsexport_index` mapping (source id -> new attachment key) for
# the current page.
# Placeholder pattern for the inline image-src form.  We accept
# both the plain `[[bsexport:kind:id]]` and the html2text-escaped
# `\[\[bsexport:kind:id\]\]` variant.  Each leading backslash is
# made optional so the regex matches either form; only the
# `image` / `attachment` kinds are included because the importer
# never creates attachment rows for `page` / `chapter` / `book`.
# The two variants share the same placeholder body so we can use
# one substitution callback for both forms.
_PLACEHOLDER_BODY = (
    r"\\?\["
    r"\\?\["
    r"bsexport:"
    r"(?P<kind>image|attachment)"
    r":(?P<id>\d+)"
    r"\\?\]"
    r"\\?\]"
)

_IMG_BSEXPORT_HTML_RE = __import__("re").compile(
    r'(?P<full><img\s[^>]*?src=")'
    + _PLACEHOLDER_BODY
    + r'("[^>]*>)',
    __import__("re").IGNORECASE,
)
_IMG_BSEXPORT_MD_RE = __import__("re").compile(
    r"(?P<full>!\[[^\]]*\]\()"
    + _PLACEHOLDER_BODY
    + r"(\))",
)

# Cross-references: [[bsexport:type:id]] -- in plain prose.
_BSEXPORT_RE = __import__("re").compile(
    r"\[\[bsexport:(?P<kind>image|attachment|page|chapter|book):(?P<id>\d+)\]\]"
)

# Same as above but for the backslash-escaped form that
# :mod:`html2text` emits when the original HTML carried brackets in
# an attribute value (e.g. an image src).  Without this the second-pass
# rewrite would leave `\[\[bsexport:...\]\]` text in place and the
# link step would try to fetch a bogus key.
_BSEXPORT_ESCAPED_RE = __import__("re").compile(
    r"\\?\["
    r"\\?\["
    r"bsexport:"
    r"(?P<kind>image|attachment|page|chapter|book)"
    r":(?P<id>\d+)"
    r"\\?\]"
    r"\\?\]"
)


class BookstackHtmlConverter:
    """HTML / Markdown converter for the BookStack importer.

    Args:
        attachment_url_builder: turns an attachment key into the URL
            that should replace an ``files/<filename>`` reference.
            Used for images and PDFs (current behaviour).
        attachment_link_builder: turns an attachment key into the
            URL used for general file attachments (anything that is
            not an image or PDF).  Defaults to
            :data:`attachment_url_builder` so older callers and
            tests that only configure the image URL still work.
        bodywidth: forwarded to :func:`html2text.html2text`.  ``0``
            disables line wrapping (we want the resulting Markdown to
            match the project's "no auto-wrap" style).
    """

    def __init__(
        self,
        attachment_url_builder: AttachmentUrlBuilder,
        *,
        attachment_link_builder: Optional[AttachmentUrlBuilder] = None,
        bodywidth: int = 0,
    ) -> None:
        self._url_builder = attachment_url_builder
        self._link_builder = attachment_link_builder or attachment_url_builder
        self._bodywidth = bodywidth

    def _format_reference(
        self,
        filename: str,
        new_key: str,
        *,
        link_text: Optional[str] = None,
    ) -> str:
        """Render a reference to an attachment as inline image or link.

        Image / PDF files keep the existing inline image URL;
        everything else is rendered as ``[link_text](url)`` so the
        markdown viewer shows a clickable link instead of trying to
        embed a non-image (which would break or render as a broken
        icon).
        """
        if is_image_or_pdf_attachment(filename):
            return self._url_builder(new_key)
        return f"[{link_text or filename}]({self._link_builder(new_key)})"

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
        *,
        bsexport_index: Optional[Dict[int, str]] = None,
        attachment_meta: Optional[Dict[int, str]] = None,
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
            bsexport_index: optional source id -> new attachment key
                map for inline ``[[bsexport:image:N]]`` /
                ``[[bsexport:attachment:N]]`` cross-refs that point
                at a known attachment.
            attachment_meta: optional source id -> original filename
                map for ``attachment`` kind cross-refs.  Used as the
                link text when an attachment is rendered as a
                markdown link instead of an inline image.

        Returns:
            str: the converted markdown body.
        """
        if page.markdown and page.markdown.strip():
            body = page.markdown
        elif page.html:
            body = self.html_to_markdown(page.html)
        else:
            return ""
        return self.rewrite_image_sources(
            body,
            file_index,
            bsexport_index=bsexport_index,
            attachment_meta=attachment_meta,
        )

    def rewrite_image_sources(
        self,
        content: str,
        file_index: Dict[str, str],
        *,
        bsexport_index: Optional[Dict[int, str]] = None,
        attachment_meta: Optional[Dict[int, str]] = None,
    ) -> str:
        """Replace ``files/<filename>`` references with attachment URLs.

        Operates on both ``<img src="filename">`` (HTML) and
        ``![alt](filename)`` (Markdown) forms.  Filenames not in
        `file_index` are left alone -- this typically happens for
        BookStack link-only attachments (no file to fetch) or for
        pages where the inline image was added via the gallery but
        not declared in the page payload.

        Image / PDF filenames keep the existing inline image URL;
        every other file type is rendered as a markdown link
        ``[filename](url)`` so the renderer does not try to embed
        something it cannot display.

        `bsexport_index` is the same shape as before (source id ->
        new attachment key) and is only consulted for inline
        ``<img src="[[bsexport:kind:N]]">`` cross-refs.  The
        `image` kind is always an image (per BookStack's gallery
        model) so it always uses the inline image URL; the
        `attachment` kind is looked up in `attachment_meta` to find
        the original filename and then routed through the same
        image-vs-link decision.
        """
        def html_sub(match: __import__("re").Match[str]) -> str:
            filename = match.group("filename")
            new_key = self._lookup(file_index, filename)
            if new_key is None:
                return match.group(0)
            if is_image_or_pdf_attachment(filename):
                return f'{match.group("full")}{self._url_builder(new_key)}{match.group(3)}'
            return f"[{filename}]({self._link_builder(new_key)})"

        def md_sub(match: __import__("re").Match[str]) -> str:
            filename = match.group("filename")
            new_key = self._lookup(file_index, filename)
            if new_key is None:
                return match.group(0)
            if is_image_or_pdf_attachment(filename):
                return f'{match.group("full")}{self._url_builder(new_key)}{match.group(3)}'
            return f"[{filename}]({self._link_builder(new_key)})"

        def bsexport_sub(
            match: __import__("re").Match[str], trailing: str
        ) -> str:
            try:
                source_id = int(match.group("id"))
            except ValueError:
                return match.group(0)
            new_key = bsexport_index.get(source_id)
            if new_key is None:
                return match.group(0)
            kind = match.group("kind")
            # `image` kind is always an image (BookStack's gallery
            # model); route it through the image URL without the
            # image/PDF check, which would otherwise reject the
            # placeholder filename we synthesize for unknown ids.
            if kind == "image":
                replacement = self._url_builder(new_key)
                return f'{match.group("full")}{replacement}{trailing}'
            # `attachment` kind: drop the original `![alt](...)` /
            # `<img ...>` wrapper and emit a markdown link in its
            # place, regardless of whether the source was HTML or
            # markdown (the importer always produces markdown).
            filename = _resolve_attachment_filename(
                kind, source_id, attachment_meta
            )
            return self._format_reference(
                filename, new_key, link_text=filename
            )

        def bsexport_html_sub(match: __import__("re").Match[str]) -> str:
            return bsexport_sub(match, match.group(4))

        def bsexport_md_sub(match: __import__("re").Match[str]) -> str:
            return bsexport_sub(match, match.group(4))

        content = _IMG_HTML_RE.sub(html_sub, content)
        content = _IMG_MD_RE.sub(md_sub, content)
        if bsexport_index:
            content = _IMG_BSEXPORT_HTML_RE.sub(bsexport_html_sub, content)
            content = _IMG_BSEXPORT_MD_RE.sub(bsexport_md_sub, content)
        return content

    def rewrite_cross_references(
        self,
        content: str,
        id_index: Dict[str, Dict[int, str]],
        attachment_url_builder: Optional[AttachmentUrlBuilder] = None,
        attachment_meta: Optional[Dict[int, str]] = None,
    ) -> str:
        """Rewrite ``[[bsexport:type:id]]`` cross-refs to attachment URLs.

        `id_index` maps the cross-ref kind (``"image"`` /
        ``"attachment"`` / ``"page"`` / ...) to a mapping of source id
        -> new attachment key.  For image kinds the value is fed
        through the URL builder (or the one configured on this
        instance) and the cross-ref is replaced with just the URL;
        for attachment kinds the replacement becomes a markdown
        link ``[filename](url)`` so non-image files render as
        clickable links.  For the page / chapter / book kinds the
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
            if kind == "image":
                return url_builder(target)
            if kind == "attachment":
                filename = _resolve_attachment_filename(
                    kind, source_id, attachment_meta
                )
                return self._format_reference(
                    filename, target, link_text=filename
                )
            return ""

        content = _BSEXPORT_RE.sub(sub, content)
        content = _BSEXPORT_ESCAPED_RE.sub(sub, content)
        return content

    @staticmethod
    def _lookup(file_index: Dict[str, str], filename: str) -> Optional[str]:
        # Tolerate an absolute-looking path inside the zip; we only
        # ever index by the basename-under-files/ form.
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
    just return a stable placeholder so the link still renders; the
    image path is also handled by :meth:`_format_reference` which
    short-circuits to the inline image URL for image / PDF
    filenames, so the placeholder rarely matters in practice.
    For ``"attachment"`` we look up the original filename from
    `attachment_meta` and fall back to a generic placeholder when
    the caller did not pass any metadata.
    """
    if attachment_meta is not None and source_id in attachment_meta:
        return attachment_meta[source_id]
    if kind == "image":
        return f"image-{source_id}"
    return f"attachment-{source_id}"


__all__ = ["BookstackHtmlConverter", "AttachmentUrlBuilder"]