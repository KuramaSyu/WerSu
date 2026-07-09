"""Unit tests for :class:`src.services.thirdparty_migrations.bookstack_reader.BookstackBookReader`.

The reader only depends on :mod:`zipfile` and :mod:`json`, so the
tests build tiny in-memory zips and assert the dataclasses they
produce.  One optional test exercises the reader against the real
``dev.zip`` attachment when the file is present on disk.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from src.services.thirdparty_migrations.bookstack_models import (
    BookstackBook,
    BookstackChapter,
    BookstackPage,
)
from src.services.thirdparty_migrations.bookstack_reader import (
    BookstackBookReader,
    BookstackZipError,
)


def _build_zip(data: dict, files: dict[str, bytes] | None = None) -> bytes:
    """Build an in-memory BookStack-style zip and return its bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.json", json.dumps(data))
        for name, payload in (files or {}).items():
            zf.writestr(f"files/{name}", payload)
    return buf.getvalue()


def test_parses_minimal_book() -> None:
    data = {"book": {"name": "Minimal"}}
    book = BookstackBookReader().read(_build_zip(data))
    assert isinstance(book, BookstackBook)
    assert book.name == "Minimal"
    assert book.chapters == []
    assert book.pages == []
    assert book.files == {}


def test_parses_cover_and_files() -> None:
    data = {
        "book": {
            "id": 7,
            "name": "Cookbook",
            "description_html": "<p>Recipes</p>",
            "cover": "cover.png",
        }
    }
    files = {"cover.png": b"PNG-BYTES", "other.jpg": b"JPG"}
    book = BookstackBookReader().read(_build_zip(data, files))
    assert book.id == 7
    assert book.description_html == "<p>Recipes</p>"
    assert book.cover == "cover.png"
    assert book.files == {"cover.png": b"PNG-BYTES", "other.jpg": b"JPG"}


def test_parses_chapters_with_pages() -> None:
    data = {
        "book": {
            "name": "B",
            "chapters": [
                {
                    "id": 1,
                    "name": "Ch1",
                    "description_html": "<p>d</p>",
                    "priority": 1,
                    "pages": [
                        {
                            "id": 10,
                            "name": "P10",
                            "markdown": "# hi",
                            "priority": 0,
                            "images": [{"id": 100, "name": "img", "file": "a.png"}],
                        },
                        {
                            "id": 11,
                            "name": "P11",
                            "html": "<p>x</p>",
                            "priority": 1,
                        },
                    ],
                }
            ],
            "pages": [{"id": 99, "name": "TopLevel", "markdown": "x", "priority": 0}],
        }
    }
    book = BookstackBookReader().read(_build_zip(data))
    assert [c.id for c in book.chapters] == [1]
    assert [c.priority for c in book.chapters] == [1]
    chapter = book.chapters[0]
    assert chapter.name == "Ch1"
    assert chapter.description_html == "<p>d</p>"
    # Pages within a chapter must be sorted by priority
    assert [p.id for p in chapter.pages] == [10, 11]
    # Direct child pages have chapter_id == None
    assert book.pages[0].id == 99
    assert book.pages[0].chapter_id is None
    # Images are preserved with their numeric id so cross-refs can be
    # rewritten later.
    assert chapter.pages[0].images[0].id == 100
    assert chapter.pages[0].images[0].file == "a.png"


def test_unknown_fields_are_ignored() -> None:
    data = {
        "book": {
            "name": "B",
            "totally_unknown_field": "ignored",
            "chapters": [
                {
                    "id": 1,
                    "name": "C",
                    "made_up": 42,
                    "pages": [
                        {"id": 9, "name": "P", "weird": [1, 2, 3]},
                    ],
                }
            ],
        }
    }
    book = BookstackBookReader().read(_build_zip(data))
    assert book.chapters[0].pages[0].name == "P"


def test_rejects_non_zip_bytes() -> None:
    with pytest.raises(BookstackZipError):
        BookstackBookReader().read(b"not a zip at all")


def test_rejects_missing_data_json() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("files/x.png", b"x")
    with pytest.raises(BookstackZipError, match="missing"):
        BookstackBookReader().read(buf.getvalue())


def test_rejects_malformed_json() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.json", "not json {{{")
    with pytest.raises(BookstackZipError, match="not valid JSON"):
        BookstackBookReader().read(buf.getvalue())


def test_rejects_export_without_book() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.json", json.dumps({"chapter": {}}))
    with pytest.raises(BookstackZipError, match="no 'book' key"):
        BookstackBookReader().read(buf.getvalue())


def test_accepts_none_chapter_pages() -> None:
    """``chapters[].pages`` may be null; should yield zero pages."""
    data = {
        "book": {
            "name": "B",
            "chapters": [{"id": 1, "name": "C", "pages": None}],
        }
    }
    book = BookstackBookReader().read(_build_zip(data))
    assert book.chapters[0].pages == []


def test_ignores_entries_outside_files_prefix() -> None:
    """Top-level files (other than data.json) should be ignored."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.json", json.dumps({"book": {"name": "B"}}))
        zf.writestr("README.txt", "noise")
        zf.writestr("files/a.png", b"A")
    book = BookstackBookReader().read(buf.getvalue())
    assert book.files == {"a.png": b"A"}


def test_attachment_with_only_link_is_kept() -> None:
    """Link-only attachments cannot be uploaded; they stay in the model."""
    data = {
        "book": {
            "name": "B",
            "pages": [
                {
                    "id": 1,
                    "name": "P",
                    "attachments": [{"id": 5, "name": "ext", "link": "https://x"}],
                }
            ],
        }
    }
    book = BookstackBookReader().read(_build_zip(data))
    att = book.pages[0].attachments[0]
    assert att.id == 5
    assert att.link == "https://x"
    assert att.file is None


# ---- Optional integration test against the real dev.zip ---------------

DEV_ZIP_PATH = Path("C:/Users/paulz/Downloads/dev.zip")


@pytest.mark.skipif(
    not DEV_ZIP_PATH.exists(),
    reason="dev.zip not present on this machine",
)
def test_real_dev_zip_parses() -> None:
    """Round-trip parse against the dev.zip attachment used as reference."""
    content = DEV_ZIP_PATH.read_bytes()
    book = BookstackBookReader().read(content)
    assert book.name == "Dev"
    assert book.id == 6
    assert book.cover == "8Y5GJlGzSb5QBUwyQTpg.png"
    assert len(book.chapters) == 13
    assert len(book.pages) == 16  # direct child pages
    # every chapter has at least one page
    assert all(ch.pages for ch in book.chapters)
    # every file referenced from data.json was loaded under files/
    assert book.files[book.cover]  # cover bytes present
    # image / attachment cross-ref targets exist in the file index when
    # they reference a real file
    image_files = {
        img.file
        for ch in book.chapters
        for pg in ch.pages
        for img in pg.images
    }
    missing = {f for f in image_files if f not in book.files}
    assert not missing, f"images[] references missing files: {sorted(missing)}"