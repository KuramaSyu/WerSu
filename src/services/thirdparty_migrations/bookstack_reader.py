"""Parse a BookStack portable zip into :class:`BookstackBook` objects.

The reader is intentionally permissive: unknown fields in
``data.json`` are dropped silently, missing optional fields default
to empty values, and a malformed zip raises a small set of typed
errors the caller can map to gRPC status codes.

Entry points:
    - :func:`read_bookstack_zip`: read a complete zip from raw bytes.
    - :class:`BookstackBookReader`: same, exposed as a class for tests
      that want to swap the json parser.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any, BinaryIO, Dict, List

from .bookstack_models import (
    BookstackAttachment,
    BookstackBook,
    BookstackChapter,
    BookstackImage,
    BookstackPage,
    BookstackTag,
)


class BookstackZipError(ValueError):
    """Raised when the zip cannot be parsed as a BookStack export."""


class BookstackBookReader:
    """Parse a BookStack portable zip into a :class:`BookstackBook`.

    The reader holds no state of its own beyond the json callable used
    to decode ``data.json``.  Tests can inject a custom callable to
    simulate malformed payloads without monkey-patching
    :mod:`json`.

    Args:
        json_loads: callable used to decode ``data.json``. Defaults to
            :func:`json.loads`.
    """

    DATA_FILENAME = "data.json"
    FILES_PREFIX = "files/"

    def __init__(self, json_loads=json.loads) -> None:
        self._json_loads = json_loads

    def read(self, content: bytes) -> BookstackBook:
        """Parse `content` (a full BookStack zip) into a :class:`BookstackBook`.

        Raises:
            BookstackZipError: if the bytes are not a zip, the zip has
                no ``data.json``, ``data.json`` is not valid JSON, the
                JSON has no ``book`` key, or a referenced file is
                missing.
        """
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                return self._read_zipfile(zf)
        except zipfile.BadZipFile as exc:
            raise BookstackZipError(f"not a valid zip file: {exc}") from exc

    def _read_zipfile(self, zf: zipfile.ZipFile) -> BookstackBook:
        """ensures that book key is present and parses files out out files/"""
        # parse json
        try:
            data = self._json_loads(zf.read(self.DATA_FILENAME).decode("utf-8"))
        except KeyError as exc:
            raise BookstackZipError(
                f"zip is missing required {self.DATA_FILENAME!r}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise BookstackZipError(
                f"{self.DATA_FILENAME} is not valid JSON: {exc}"
            ) from exc

        # json starts with {book: ...}
        if not isinstance(data, dict) or "book" not in data:
            # json does not has a top-level "book" key
            raise BookstackZipError(
                f"{self.DATA_FILENAME} has no 'book' key; only book exports are supported"
            )

        book_payload = data["book"] or {}
        if not isinstance(book_payload, dict):
            raise BookstackZipError("'book' entry is not an object")

        files = self._read_files(zf)
        return self._build_book(book_payload, files)

    def _read_files(self, zf: zipfile.ZipFile) -> Dict[str, bytes]:
        """
        checks files/ in the zip and returns it as mapping from
        filename -> raw bytes
        """
        files: Dict[str, bytes] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not info.filename.startswith(self.FILES_PREFIX):
                continue
            short = info.filename[len(self.FILES_PREFIX):]
            if not short:
                continue
            files[short] = zf.read(info)
        return files

    def _build_book(
        self, payload: Dict[str, Any], files: Dict[str, bytes]
    ) -> BookstackBook:
        chapters_payload = payload.get("chapters") or []
        direct_pages_payload = payload.get("pages") or []

        chapters: List[BookstackChapter] = [
            self._build_chapter(ch) for ch in chapters_payload if isinstance(ch, dict)
        ]
        for chapter in chapters:
            chapter.pages = sorted(chapter.pages, key=lambda p: p.priority)

        direct_pages: List[BookstackPage] = [
            self._build_page(pg, chapter_id=None)
            for pg in direct_pages_payload
            if isinstance(pg, dict)
        ]
        direct_pages.sort(key=lambda p: p.priority)

        return BookstackBook(
            id=payload.get("id"),
            name=str(payload.get("name") or "Untitled book"),
            description_html=str(payload.get("description_html") or ""),
            cover=payload.get("cover"),
            chapters=sorted(chapters, key=lambda c: c.priority),
            pages=direct_pages,
            tags=[self._build_tag(t) for t in (payload.get("tags") or [])],
            files=files,
        )

    def _build_chapter(self, payload: Dict[str, Any]) -> BookstackChapter:
        chapter_id = int(payload.get("id") or 0)
        pages = [
            self._build_page(pg, chapter_id=chapter_id)
            for pg in (payload.get("pages") or [])
            if isinstance(pg, dict)
        ]
        return BookstackChapter(
            id=chapter_id,
            name=str(payload.get("name") or "Untitled chapter"),
            description_html=str(payload.get("description_html") or ""),
            priority=int(payload.get("priority") or 0),
            pages=pages,
            tags=[self._build_tag(t) for t in (payload.get("tags") or [])],
        )

    def _build_page(
        self, payload: Dict[str, Any], *, chapter_id: int | None
    ) -> BookstackPage:
        return BookstackPage(
            id=int(payload.get("id") or 0),
            name=str(payload.get("name") or "Untitled page"),
            html=str(payload.get("html") or ""),
            markdown=str(payload.get("markdown") or ""),
            priority=int(payload.get("priority") or 0),
            chapter_id=chapter_id,
            images=[
                self._build_image(img)
                for img in (payload.get("images") or [])
                if isinstance(img, dict)
            ],
            attachments=[
                self._build_attachment(att)
                for att in (payload.get("attachments") or [])
                if isinstance(att, dict)
            ],
            tags=[self._build_tag(t) for t in (payload.get("tags") or [])],
        )

    def _build_image(self, payload: Dict[str, Any]) -> BookstackImage:
        return BookstackImage(
            id=int(payload.get("id") or 0),
            name=str(payload.get("name") or ""),
            file=str(payload.get("file") or ""),
            type=str(payload.get("type") or "gallery"),
        )

    def _build_attachment(self, payload: Dict[str, Any]) -> BookstackAttachment:
        file = payload.get("file")
        link = payload.get("link")
        return BookstackAttachment(
            id=int(payload.get("id") or 0),
            name=str(payload.get("name") or ""),
            file=str(file) if file else None,
            link=str(link) if link else None,
        )

    def _build_tag(self, payload: Dict[str, Any]) -> BookstackTag:
        return BookstackTag(
            name=str(payload.get("name") or ""),
            value=str(payload.get("value") or ""),
        )


def read_bookstack_zip(content: bytes) -> BookstackBook:
    """Convenience wrapper around :meth:`BookstackBookReader.read`."""
    return BookstackBookReader().read(content)


__all__ = [
    "BookstackBookReader",
    "BookstackZipError",
    "read_bookstack_zip",
]