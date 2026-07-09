"""Dataclasses mirroring the BookStack portable-zip export format.

The shape of these dataclasses matches the JSON documented at
https://github.com/BookStackApp/BookStack/blob/development/dev/docs/portable-zip-file-format.md
and matches the ``data.json`` shape produced by BookStack's "Export
Book" feature.  Only the fields the import actually consumes are
typed; the rest are dropped at parse time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class BookstackTag:
    """One ``{name, value}`` entry on a book / chapter / page."""

    name: str
    value: str


@dataclass
class BookstackImage:
    """An image embedded in a page.

    `file` is the filename under ``files/`` in the export zip;
    `name` is the original display name; `id` is the source
    BookStack numeric id (kept so we can rewrite ``[[bsexport:image:N]]``
    cross-references).
    """

    id: int
    name: str
    file: str
    type: str = "gallery"


@dataclass
class BookstackAttachment:
    """An attachment on a page.

    Either `link` (an external URL the export kept as-is) or `file`
    (a filename under ``files/``) is set, never both.  Only file-based
    attachments can be imported; link-only entries are surfaced as
    warnings and skipped.
    """

    id: int
    name: str
    file: Optional[str] = None
    link: Optional[str] = None


@dataclass
class BookstackPage:
    """One BookStack page.

    `html` and `markdown` are mutually optional in the export; the
    importer prefers `markdown` when non-empty.  `chapter_id` is
    :obj:`None` for direct child pages of the book (BookStack stores
    those in ``book.pages`` rather than under any chapter).

    Attributes:
        id: source BookStack numeric id.
        name: page title.
        html: page body as HTML.
        markdown: page body as Markdown.
        priority: ordering within the chapter / book; lower comes first.
        chapter_id: parent chapter id, or :obj:`None` if direct child.
        images: explicit image entries declared on the page.
        attachments: attachment entries declared on the page.
        tags: tags declared on the page.
    """

    id: int
    name: str
    html: str = ""
    markdown: str = ""
    priority: int = 0
    chapter_id: Optional[int] = None
    images: List[BookstackImage] = field(default_factory=list)
    attachments: List[BookstackAttachment] = field(default_factory=list)
    tags: List[BookstackTag] = field(default_factory=list)


@dataclass
class BookstackChapter:
    """One chapter inside a book.

    Attributes:
        id: source BookStack numeric id.
        name: chapter title.
        description_html: chapter description as HTML.
        priority: ordering within the book; lower comes first.
        pages: pages that live inside this chapter.
        tags: tags declared on the chapter.
    """

    id: int
    name: str
    description_html: str = ""
    priority: int = 0
    pages: List[BookstackPage] = field(default_factory=list)
    tags: List[BookstackTag] = field(default_factory=list)


@dataclass
class BookstackBook:
    """The top-level entity in ``data.json``.

    `files` carries the raw bytes of every entry under the
    ``files/`` directory of the export zip, keyed by filename.  The
    reader pre-loads this so the importer can stream attachments in
    one pass.

    Attributes:
        id: source BookStack numeric id (optional in exports).
        name: book title.
        description_html: book description as HTML.
        cover: filename of the cover image under ``files/``, if any.
        chapters: chapters in source order (the importer re-sorts by priority).
        pages: direct child pages (those not inside any chapter).
        tags: tags declared on the book.
        files: filename -> raw bytes mapping from the zip.
    """

    name: str
    files: dict[str, bytes] = field(default_factory=dict)
    id: Optional[int] = None
    description_html: str = ""
    cover: Optional[str] = None
    chapters: List[BookstackChapter] = field(default_factory=list)
    pages: List[BookstackPage] = field(default_factory=list)
    tags: List[BookstackTag] = field(default_factory=list)


__all__ = [
    "BookstackTag",
    "BookstackImage",
    "BookstackAttachment",
    "BookstackPage",
    "BookstackChapter",
    "BookstackBook",
]