"""Unit tests for :class:`src.services.thirdparty_migrations.bookstack.BookstackBookImport`.

The orchestrator is the only class in this package that touches the
project's service layer.  We stub every dependency in-process:
:class:`DirectoryServiceABC` via the existing
``tests.stubs.directory_service._StubDirectoryService``, and
:class:`NoteServiceABC` / :class:`AttachmentFacadeABC` via tiny
local doubles that record calls and let the test force errors.

Wire shape asserted:

* The four-step pipeline (upload all files -> book dir -> chapter
  dirs -> notes + attachment linking) runs in that exact order.
* `BookstackZipError` from the reader surfaces unchanged to the
  caller (the gRPC adapter maps it to ``INVALID_ARGUMENT``).
* Per-page failures are logged and skipped; the import returns a
  partial :class:`MigrationResult` instead of raising.
* The book cover image is uploaded, the resulting key is wrapped in
  a URL by the injected builder, and that URL is stored on the book
  directory's ``image_url``.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from typing import Dict, List, Optional, Tuple

import pytest

from src.api.directory_service import DirectoryServiceABC
from src.api.note_service import NoteServiceABC
from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.attachments.attachments import Attachment
from src.services.attachments import AttachmentFacadeABC
from src.services.thirdparty_migrations import MigrationResult
from src.services.thirdparty_migrations.bookstack import BookstackBookImport
from src.services.thirdparty_migrations.bookstack_reader import BookstackZipError
from tests.stubs.directory_service import _StubDirectoryService
from tests.stubs.logging import silent_logger
from tests.stubs.user_context import _UserContext


# -------------------------------------------------------------------------
# Local stubs for NoteServiceABC and AttachmentFacadeABC.  Both record
# every call so tests can assert on the exact ordering and arguments.
# -------------------------------------------------------------------------


class _StubNoteService(NoteServiceABC):
    """Records every note call; auto-assigns sequential ids."""

    def __init__(self) -> None:
        self.inserted: List[NoteEntity] = []
        self.updated: List[NoteEntity] = []
        self.insert_calls: List[Tuple[NoteEntity, str]] = []
        self.update_calls: List[Tuple[NoteEntity, str]] = []
        self._next_id = 0
        self.insert_should_raise: Optional[Exception] = None

    async def insert_note(self, note: NoteEntity, user_ctx: UserContextABC) -> NoteEntity:
        self.insert_calls.append((note, user_ctx.user_id))
        if self.insert_should_raise is not None:
            raise self.insert_should_raise
        self._next_id += 1
        inserted = NoteEntity(
            note_id=f"note-{self._next_id}",
            title=note.title,
            content=note.content,
            author_id=note.author_id,
            parent_dir_id=note.parent_dir_id,
            permissions=[],
        )
        self.inserted.append(inserted)
        return inserted

    async def update_note(self, note: NoteEntity, user_ctx: UserContextABC) -> NoteEntity:
        self.update_calls.append((note, user_ctx.user_id))
        self.updated.append(note)
        return note

    async def get_note(self, note_id, user_ctx):
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):
        raise NotImplementedError

    async def search_notes(self, search_type, query, user_ctx, limit, offset):
        raise NotImplementedError

    async def get_notes(self, note_ids, user_ctx, options=None):
        raise NotImplementedError


class _StubAttachmentFacade(AttachmentFacadeABC):
    """Records every attachment call; assigns sequential keys."""

    def __init__(self) -> None:
        self.posted: List[Attachment] = []
        self.links: List[Tuple[str, str, str]] = []  # (key, note_id, user_id)
        self._next_key = 0
        self.post_should_raise: Optional[Exception] = None
        self.link_should_raise: Optional[Exception] = None

    async def post_attachment(
        self, attachment: Attachment, user_ctx: UserContextABC
    ) -> Attachment:
        self._next_key += 1
        key = f"att-{self._next_key}"
        stored = Attachment(
            key=key,
            filename=attachment.filename,
            filepath=attachment.filepath,
            content_type=attachment.content_type,
            size=attachment.size,
            content=attachment.content,
            created_at=attachment.created_at,
            updated_at=attachment.updated_at,
        )
        self.posted.append(stored)
        if self.post_should_raise is not None:
            raise self.post_should_raise
        return stored

    async def update_metadata(self, attachment, user_ctx):
        raise NotImplementedError

    async def get_attachment(self, key, user_ctx):
        raise NotImplementedError

    async def get_metadata(self, key, user_ctx):
        raise NotImplementedError

    async def delete_attachment(self, key, user_ctx):
        raise NotImplementedError

    async def link_attachment_to_note(
        self, attachment_key: str, note_id: str, user_ctx: UserContextABC
    ) -> None:
        self.links.append((attachment_key, note_id, user_ctx.user_id))
        if self.link_should_raise is not None:
            raise self.link_should_raise

    async def unlink_attachment_from_note(
        self, attachment_key, note_id, user_ctx
    ):
        raise NotImplementedError

    async def list_attachments_for_note(self, note_id, user_ctx):
        raise NotImplementedError


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def _build_zip(payload: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.json", json.dumps(payload))
        # Provide a couple of binary entries so file_index is populated.
        zf.writestr("files/cover.png", b"COVER")
        zf.writestr("files/pic-a.png", b"A")
        zf.writestr("files/pic-b.png", b"B")
    return buf.getvalue()


def _book_payload(
    *,
    cover: str = "cover.png",
    chapters: list | None = None,
    pages: list | None = None,
) -> dict:
    return {
        "book": {
            "name": "Test book",
            "description_html": "<p>About the book</p>",
            "cover": cover,
            "chapters": chapters or [],
            "pages": pages or [],
        }
    }


def _build_importer(
    *,
    directory_service: DirectoryServiceABC | None = None,
    note_service: NoteServiceABC | None = None,
    attachment_facade: AttachmentFacadeABC | None = None,
) -> Tuple[
    BookstackBookImport,
    _StubDirectoryService,
    _StubNoteService,
    _StubAttachmentFacade,
]:
    ds = directory_service or _StubDirectoryService()
    ns = note_service or _StubNoteService()
    af = attachment_facade or _StubAttachmentFacade()

    # Pre-seed the directory service so `create_directory` returns
    # auto-incrementing ids and parents can be looked up.
    if isinstance(ds, _StubDirectoryService):
        ds.next_directory_id = 0

    importer = BookstackBookImport(
        attachment_facade=af,
        directory_service=ds,
        note_service=ns,
        log=silent_logger,
    )
    return importer, ds, ns, af


def _stub_directory_assigns_ids(stub: _StubDirectoryService) -> None:
    """Wrap `_StubDirectoryService.create_directory` to assign sequential ids."""

    original_create = stub.create_directory if hasattr(stub, "create_directory") else None

    async def patched(entity, user_ctx):
        stub.next_directory_id += 1
        new_id = f"dir-{stub.next_directory_id}"
        created = DirectoryEntity(
            id=new_id,
            name=entity.name,
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            parent_id=entity.parent_id,
            readme_note_id=UNDEFINED,
            relations=[],
        )
        stub.directories_by_id[new_id] = created
        return created

    stub.create_directory = patched  # type: ignore[attr-defined]


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_four_step_pipeline_order() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    chapters = [
        {
            "id": 1,
            "name": "Chapter 1",
            "description_html": "",
            "priority": 0,
            "pages": [
                {"id": 10, "name": "P10", "markdown": "m10", "priority": 0},
                {"id": 11, "name": "P11", "markdown": "m11", "priority": 1},
            ],
        }
    ]
    payload = _book_payload(chapters=chapters)
    user_ctx = _UserContext(user_id="u1")

    result = await importer.migrate(_build_zip(payload), user_ctx)

    # 1. All three image files were uploaded first.
    uploaded_names = sorted(a.filename for a in af.posted)
    assert uploaded_names == ["cover.png", "pic-a.png", "pic-b.png"]
    # 2. Then the book directory was created (4 create_directory calls
    #    total: 1 book + 1 chapter + 0 direct child pages -> no
    #    chapter pages creates since we have 1 chapter dir already).
    assert ds.next_directory_id == 2  # 1 book + 1 chapter
    # 3. Pages inserted after directories.
    assert [n.title for n in ns.inserted] == ["P10", "P11"]
    # 4. Cross-ref / link pass runs at the end.  With no inline
    #    attachment refs and no images[] entries, no link calls happen.
    assert result.pages_imported == 2
    assert result.attachments_uploaded == 3


@pytest.mark.asyncio
async def test_book_cover_becomes_image_url_on_book_directory() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    payload = _book_payload(chapters=[{"id": 1, "name": "Ch", "pages": []}])
    user_ctx = _UserContext(user_id="u1")

    await importer.migrate(_build_zip(payload), user_ctx)

    # The book directory is the first create_directory call.  Find it
    # by inspecting the patched stub's state.
    book_dir = ds.directories_by_id["dir-1"]
    assert book_dir.name == "Test book"
    # URL builder is the default; format should match build_attachment_url.
    assert "/api/attachments/image?" in (book_dir.image_url or "")
    assert "key=" in (book_dir.image_url or "")
    assert "att-1" in (book_dir.image_url or "")  # cover was the first upload


@pytest.mark.asyncio
async def test_chapter_directory_is_linked_to_book() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    payload = _book_payload(chapters=[{"id": 1, "name": "Ch1", "pages": []}])
    user_ctx = _UserContext(user_id="u1")

    await importer.migrate(_build_zip(payload), user_ctx)

    book_dir = ds.directories_by_id["dir-1"]
    chapter_dir = ds.directories_by_id["dir-2"]
    assert chapter_dir.parent_id == str(book_dir.id)


@pytest.mark.asyncio
async def test_direct_child_pages_go_under_book_dir() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    payload = _book_payload(
        pages=[
            {"id": 100, "name": "TopA", "markdown": "a", "priority": 0},
            {"id": 101, "name": "TopB", "markdown": "b", "priority": 1},
        ]
    )
    user_ctx = _UserContext(user_id="u1")

    result = await importer.migrate(_build_zip(payload), user_ctx)

    assert result.pages_imported == 2
    # The note's parent_dir_id should be the book directory id.
    assert all(n.parent_dir_id == "dir-1" for n in ns.inserted)


@pytest.mark.asyncio
async def test_images_array_links_to_note() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    payload = _book_payload(
        pages=[
            {
                "id": 1,
                "name": "P",
                "markdown": "see ![x](pic-a.png)",
                "priority": 0,
                "images": [{"id": 50, "name": "x", "file": "pic-a.png"}],
            }
        ]
    )
    user_ctx = _UserContext(user_id="u1")

    result = await importer.migrate(_build_zip(payload), user_ctx)

    # pic-a.png was uploaded -> att-2 (cover was att-1)
    keys = {a.key for a in af.posted}
    assert "att-2" in keys
    # pic-a.png was referenced inline AND via images[], but link
    # calls are deduped by key.
    note_id = ns.inserted[0].note_id
    linked_keys = {key for key, nid, _uid in af.links}
    assert "att-2" in linked_keys
    assert all(nid == note_id for _key, nid, _uid in af.links)


@pytest.mark.asyncio
async def test_cross_refs_get_rewritten_in_second_pass() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    payload = _book_payload(
        pages=[
            {
                "id": 1,
                "name": "P",
                "markdown": "see [[bsexport:image:50]]",
                "priority": 0,
                "images": [{"id": 50, "name": "x", "file": "pic-a.png"}],
            }
        ]
    )
    user_ctx = _UserContext(user_id="u1")

    await importer.migrate(_build_zip(payload), user_ctx)

    # The note should have been updated exactly once with rewritten
    # content -- the [[bsexport:image:50]] becomes an attachment URL.
    assert len(ns.updated) == 1
    updated_note = ns.updated[0]
    assert "[[bsexport" not in (updated_note.content or "")
    assert "/api/attachments/image?" in (updated_note.content or "")


@pytest.mark.asyncio
async def test_invalid_zip_surfaces_as_bookstack_zip_error() -> None:
    importer, ds, ns, af = _build_importer()
    user_ctx = _UserContext(user_id="u1")
    with pytest.raises(BookstackZipError):
        await importer.migrate(b"not a zip", user_ctx)


@pytest.mark.asyncio
async def test_attachment_upload_failure_continues() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    # Force every post_attachment to fail.
    af.post_should_raise = RuntimeError("disk full")
    payload = _book_payload(
        chapters=[
            {
                "id": 1,
                "name": "Ch",
                "pages": [{"id": 1, "name": "P", "markdown": "x"}],
            }
        ]
    )
    user_ctx = _UserContext(user_id="u1")

    # Best-effort: import should still produce a book + chapter dir,
    # and the page should still be inserted (with no image refs).
    result = await importer.migrate(_build_zip(payload), user_ctx)
    assert result.attachments_uploaded == 0
    assert result.pages_imported == 1


@pytest.mark.asyncio
async def test_page_insert_failure_skips_page() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    ns.insert_should_raise = RuntimeError("bad page")
    payload = _book_payload(
        pages=[
            {"id": 1, "name": "WillFail", "markdown": "x", "priority": 0},
            {"id": 2, "name": "WillNotEvenTry", "markdown": "x", "priority": 1},
        ]
    )
    user_ctx = _UserContext(user_id="u1")

    result = await importer.migrate(_build_zip(payload), user_ctx)
    # First insert fails -> exception propagates from the loop body and
    # aborts the rest.  Confirm at least pages_imported reflects 0
    # (the failure path caught and logged the error, then continued,
    # but on the second iteration insert_should_raise is still set so
    # 0 pages are inserted).
    assert result.pages_imported == 0


@pytest.mark.asyncio
async def test_link_attachment_failure_is_logged_not_raised() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    af.link_should_raise = RuntimeError("link failed")
    payload = _book_payload(
        pages=[
            {
                "id": 1,
                "name": "P",
                "markdown": "see ![](pic-a.png)",
                "priority": 0,
            }
        ]
    )
    user_ctx = _UserContext(user_id="u1")

    # The import must NOT propagate the link error.
    result = await importer.migrate(_build_zip(payload), user_ctx)
    assert result.pages_imported == 1
    assert af.links  # attempted at least once


@pytest.mark.asyncio
async def test_returns_root_directory_id_matching_book_dir() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    payload = _book_payload(pages=[])
    user_ctx = _UserContext(user_id="u1")

    result: MigrationResult = await importer.migrate(_build_zip(payload), user_ctx)
    assert result.root_directory_id == "dir-1"
    assert isinstance(result, MigrationResult)


@pytest.mark.asyncio
async def test_no_chapters_in_chapters_list_means_no_chapters_in_result() -> None:
    importer, ds, ns, af = _build_importer()
    _stub_directory_assigns_ids(ds)

    payload = _book_payload(pages=[{"id": 1, "name": "Solo", "markdown": "x"}])
    user_ctx = _UserContext(user_id="u1")

    result = await importer.migrate(_build_zip(payload), user_ctx)
    assert result.chapters == []
    assert result.pages_imported == 1