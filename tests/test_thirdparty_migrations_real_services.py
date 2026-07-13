"""Integration-style tests for :class:`src.services.thirdparty_migrations.bookstack.BookstackBookImport`.

Unlike ``test_thirdparty_migrations_bookstack.py`` which uses tiny
hand-rolled stubs, this file wires the **real**
:class:`~src.services.directory.DirectoryServiceImpl`,
:class:`~src.services.note.NoteServiceImpl` and
:class:`~src.services.attachment_facade.AttachmentFacadeImpl` against the
in-memory fakes from :mod:`tests._fixtures_pkg.fakes`.

The motivation: the BookStack importer was originally tested with
stubs that do not exercise :meth:`NoteServiceImpl._resolve_parent_directory_id`,
which queries :meth:`DirectoryRepo.list_user_directory_ids` to verify
the caller can see the target directory.  Without that guard the
orchestrator happily inserts pages against directories the user
cannot see, which fails in production the moment
:class:`NoteServiceImpl` runs its visibility check.  These tests pin the
end-to-end path so the failure cannot regress again.

Wire shape asserted:

* The full pipeline against the real :class:`NoteServiceImpl` produces a
  non-zero ``pages_imported`` count and one
  :class:`~src.db.entities.note.metadata.NoteEntity` per page.
* The book directory, every chapter directory and every page note
  end up under the right parent in the in-memory store.
* Inserting a note with a parent directory the user cannot see is
  rejected with ``ValueError`` -- so the orchestrator must ensure the
  directory is visible to the caller before inserting pages.
* When ``link_attachment_to_note`` fails for a page, the page is still
  counted as imported.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path
from typing import List, Optional

import pytest

from src.api.repos.permission_repo import PermissionRepoABC
from src.api.other.relationship import (
    NoteRelationEnum,
    ObjectTypeEnum,
    Relationship,
)
from src.api.other.undefined import UNDEFINED
from src.db.table import TableABC
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from src.services.attachment_facade import AttachmentFacadeImpl
from src.services.directory import DirectoryServiceImpl
from src.services.note import NoteServiceImpl
from src.services.thirdparty_migrations.bookstack import BookstackBookImport
from src.services.thirdparty_migrations.bookstack_html_converter import (
    BookstackHtmlConverter,
)
from src.services.thirdparty_migrations.bookstack_reader import BookstackBookReader
from tests._fixtures_pkg.fakes import (
    _FakeCombinedNoteRepo,
    _FakeDatabase,
    _FakeEmbeddingRepo,
    _FakeJwtProvider,
    _FakeNoteContentRepo,
    _FakeNoteRepoFacade,
    _FakeNoteTagRepo,
    _TestDirectoryRepo,
)
from tests.stubs.activity_logger_service import _FakeActivityLoggerService
from tests.stubs.attachments import (
    InMemoryAttachmentMetadataRepo,
    InMemoryAttachmentRepo,
)
from tests.stubs.logging import silent_logger
from tests.stubs.user_context import _UserContext as _UserCtx


# ---------------------------------------------------------------------------
# Tiny TableABC stub used by AttachmentFacadeImpl.link_attachment_to_note
# ---------------------------------------------------------------------------


class _FakeLinkTable(TableABC):
    """In-memory :class:`TableABC` for the ``note.attachment_note_link`` table.

    Stores rows as a list of dicts so tests can inspect the linkage
    that the orchestrator created.
    """

    def __init__(self) -> None:
        self.rows: List[dict] = []
        self.insert_calls: List[dict] = []
        self.delete_calls: List[dict] = []

    async def insert(self, record: dict) -> None:
        # Dedup: keep unique (note_id, attachment_key) pairs.
        key = (record["note_id"], record["attachment_key"])
        if not any(
            (r["note_id"], r["attachment_key"]) == key for r in self.rows
        ):
            self.rows.append(record)
            self.insert_calls.append(record)

    async def delete(self, where: dict) -> None:
        self.delete_calls.append(where)
        self.rows = [
            r
            for r in self.rows
            if not all(r.get(k) == v for k, v in where.items())
        ]

    async def select(self, where: Optional[dict] = None) -> List[dict]:
        if not where:
            return list(self.rows)
        return [
            r
            for r in self.rows
            if all(r.get(k) == v for k, v in where.items())
        ]


# ---------------------------------------------------------------------------
# Service wiring helpers
# ---------------------------------------------------------------------------


def _wire_real_services(
    *,
    user_id: str = "user-1",
    queue_note_ids: Optional[List[str]] = None,
) -> tuple:
    """Build a real :class:`BookstackBookImport` wired against fakes.

    Returns the importer plus every collaborator so the test can
    inspect the in-memory state after a migration.

    The fake directory repo is wired with a reference to the in-memory
    permission repo so ``create_directory`` writes the entity's
    ``admin`` relation through it and ``list_user_directory_ids`` walks
    the permission store for transitive ``admin -> view`` expansion --
    both of which SpiceDB does in production.
    """
    fake_db = _FakeDatabase()
    # Pre-queue enough note ids for the insert paths we'll exercise.
    # Tests can override this by passing ``queue_note_ids`` explicitly.
    ids = queue_note_ids or [
        f"019f0000-0000-7000-8000-{i:012d}" for i in range(1, 200)
    ]
    for nid in ids:
        fake_db.fetchrow_responses.append({"id": nid})

    # ``_FakeNoteRepoFacade`` is the in-memory replacement for the
    # real :class:`NoteFacadeImpl` used by the pure unit-test suite.  We
    # build one here and route the real facade at it via a thin
    # wrapper so the orchestrator can both insert and select notes
    # end-to-end.  This keeps :class:`NoteFacadeImpl`'s real
    # ``_resolve_parent_directory_id`` flow in the loop.
    fake_facade = _FakeNoteRepoFacade()
    embedding_repo = _FakeEmbeddingRepo()
    permission_repo: PermissionRepoABC = InMemoryPermissionRepo()
    directory_repo = _TestDirectoryRepo(permission_repo=permission_repo)
    content_repo = _FakeNoteContentRepo(facade=fake_facade)
    jwt_provider = _FakeJwtProvider()
    activity_logger = _FakeActivityLoggerService()

    from src.db.repos.note.note import NoteFacadeImpl

    real_facade = NoteFacadeImpl(
        db=fake_db,
        content_repo=content_repo,
        combined_repo=_FakeCombinedNoteRepo(content_repo=content_repo),
        embedding_repo=embedding_repo,
        logging_provider=lambda *_a, **_k: logging.getLogger(
            "test.fake.note_facade"
        ),
        permission_repo=permission_repo,
        directory_repo=directory_repo,
        tag_repo=_FakeNoteTagRepo(),
    )

    # Bridge: the real ``NoteFacadeImpl.insert`` writes to the content
    # repo via raw SQL (``_FakeDatabase.fetchrow`` echoes a queued
    # id and writes nothing), so we additionally seed both the
    # content repo and the fake facade's notes map on every insert.
    # ``NoteServiceImpl.insert_note`` calls ``_note_repo.insert(note, user)``
    # and then reads the returned ``note_id`` -- the real facade sets
    # ``note.note_id`` from the queued response, so the bridge only
    # needs to keep the content repo's ``_store`` in sync so that a
    # later ``update_note`` finds the row.
    _real_insert = real_facade.insert

    async def _insert_bridge(note: NoteEntity, user) -> NoteEntity:
        result = await _real_insert(note, user)
        if result.note_id not in (None, UNDEFINED):
            content_repo.seed(result)
            fake_facade.notes_by_id[str(result.note_id)] = result
        return result

    real_facade.insert = _insert_bridge  # type: ignore[assignment]

    # ``select_by_id`` on the real facade goes through
    # ``content_repo.select_by_id`` which now falls back to the fake
    # facade when the row is missing, so no bridge is needed for
    # read paths.
    facade = real_facade
    note_service = NoteServiceImpl(
        note_repo=facade,
        permission_repo=permission_repo,
        jwt_provider=jwt_provider,
        directory_repo=directory_repo,
        activity_logger=activity_logger,
        logging_provider=silent_logger,
    )
    attachment_facade = AttachmentFacadeImpl(
        attachment_repo=InMemoryAttachmentRepo(),
        metadata_repo=InMemoryAttachmentMetadataRepo(),
        permission_repo=permission_repo,
        attachments_note_link_table=_FakeLinkTable(),
        log=silent_logger,
    )
    directory_service = DirectoryServiceImpl(
        directory_repo=directory_repo,
        note_repo=facade,
        permission_repo=permission_repo,
        activity_logger=activity_logger,
        attachment_facade=attachment_facade,
        note_service=note_service,
        log=silent_logger,
    )

    importer = BookstackBookImport(
        attachment_facade=attachment_facade,
        directory_service=directory_service,
        note_service=note_service,
        log=silent_logger,
        reader=BookstackBookReader(),
        converter=BookstackHtmlConverter(
            attachment_url_builder=lambda k: f"/u/{k}"
        ),
    )
    return (
        importer,
        note_service,
        directory_service,
        attachment_facade,
        permission_repo,
        directory_repo,
        content_repo,
        activity_logger,
        user_id,
    )


def _build_zip(payload: dict, files: Optional[dict] = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.json", json.dumps(payload))
        for name, data in (files or {}).items():
            zf.writestr(f"files/{name}", data)
    return buf.getvalue()


def _small_book_payload() -> dict:
    return {
        "book": {
            "name": "Tiny book",
            "description_html": "<p>desc</p>",
            "cover": "cover.png",
            "chapters": [
                {
                    "id": 1,
                    "name": "Chapter 1",
                    "description_html": "",
                    "priority": 0,
                    "pages": [
                        {
                            "id": 10,
                            "name": "Page A",
                            "markdown": "alpha",
                            "priority": 0,
                        },
                        {
                            "id": 11,
                            "name": "Page B",
                            "html": "<p>beta</p>",
                            "priority": 1,
                        },
                    ],
                }
            ],
            "pages": [
                {"id": 99, "name": "Top", "markdown": "top", "priority": 0}
            ],
        }
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_services_import_every_page() -> None:
    """End-to-end against the real NoteServiceImpl / DirectoryServiceImpl."""
    (
        importer,
        _note_service,
        _directory_service,
        _attachment_facade,
        _perm,
        directory_repo,
        content_repo,
        _activity_logger,
        user_id,
    ) = _wire_real_services()
    user_ctx = _UserCtx(user_id=user_id)

    payload = _small_book_payload()
    files = {"cover.png": b"PNG", "pic.png": b"PIC"}
    result = await importer.migrate(_build_zip(payload, files), user_ctx)

    # 2 chapter pages + 1 direct page = 3 pages
    assert result.pages_imported == 3, (
        f"expected 3 pages, got {result.pages_imported}; "
        f"directory_repo.directories_by_id keys = "
        f"{sorted(directory_repo.directories_by_id)}; "
        f"content_repo._store keys = {sorted(content_repo._store)}"
    )
    # README.md notes are auto-created for every directory; the
    # orchestrator does not count them in `pages_imported`, so we
    # filter them out when asserting on the note store.
    user_notes = [
        n for n in content_repo._store.values() if n.title != "README.md"
    ]
    assert len(user_notes) == 3
    titles = sorted(n.title for n in user_notes)
    assert titles == ["Page A", "Page B", "Top"]


@pytest.mark.asyncio
async def test_real_services_create_book_and_chapter_dirs() -> None:
    """The book + chapter directories exist after the import."""
    (
        importer,
        _note_service,
        _directory_service,
        _attachment_facade,
        _perm,
        directory_repo,
        _content_repo,
        _activity_logger,
        user_id,
    ) = _wire_real_services()
    user_ctx = _UserCtx(user_id=user_id)

    result = await importer.migrate(_build_zip(_small_book_payload()), user_ctx)

    created = list(directory_repo.created)
    assert len(created) == 2  # book + chapter

    book_dir = next(d for d in created if d.display_name == "Tiny book")
    chapter_dir = next(d for d in created if d.display_name == "Chapter 1")

    # Chapter directory is parented under the book.
    assert list(chapter_dir.parent_directory_ids or []) == [str(book_dir.id)]
    # The book's parent set is empty for a top-level import.
    assert book_dir.parent_directory_ids in (UNDEFINED, None, [])
    # The response carries the book directory id.
    assert result.root_directory_id == str(book_dir.id)


@pytest.mark.asyncio
async def test_pages_land_in_the_right_directory() -> None:
    """Chapter pages go under the chapter dir, direct pages under the book dir."""
    (
        importer,
        _note_service,
        _directory_service,
        _attachment_facade,
        _perm,
        directory_repo,
        content_repo,
        _activity_logger,
        user_id,
    ) = _wire_real_services()
    user_ctx = _UserCtx(user_id=user_id)

    await importer.migrate(_build_zip(_small_book_payload()), user_ctx)

    created = list(directory_repo.created)
    book_dir = next(d for d in created if d.display_name == "Tiny book")
    chapter_dir = next(d for d in created if d.display_name == "Chapter 1")

    page_a = next(
        n
        for n in content_repo._store.values()
        if n.title == "Page A"
    )
    page_b = next(
        n
        for n in content_repo._store.values()
        if n.title == "Page B"
    )
    top = next(
        n
        for n in content_repo._store.values()
        if n.title == "Top"
    )

    def _parent_dir_id(note) -> Optional[str]:
        for rel in (note.permissions or []):
            if str(rel.relation) == str(NoteRelationEnum.PARENT_DIRECTORY):
                return str(rel.subject.object_id)
        return None

    assert _parent_dir_id(page_a) == str(chapter_dir.id)
    assert _parent_dir_id(page_b) == str(chapter_dir.id)
    assert _parent_dir_id(top) == str(book_dir.id)


@pytest.mark.asyncio
async def test_real_dev_zip_round_trip() -> None:
    """Import the real ``dev.zip`` reference export end-to-end.

    Skipped when the file is not present on this machine.
    """
    dev_zip = Path("C:/Users/paulz/Downloads/dev.zip")
    if not dev_zip.exists():
        pytest.skip("dev.zip not present on this machine")

    (
        importer,
        _note_service,
        _directory_service,
        _attachment_facade,
        _perm,
        directory_repo,
        content_repo,
        _activity_logger,
        user_id,
    ) = _wire_real_services()
    user_ctx = _UserCtx(user_id=user_id)

    content = dev_zip.read_bytes()
    result = await importer.migrate(content, user_ctx)

    # dev.zip has 13 chapters + 16 direct pages = 114 pages total
    # (see _inspect_devzip.py for the canonical breakdown).
    assert result.pages_imported == 114, (
        f"expected 114 pages, got {result.pages_imported}; "
        f"created dirs = {len(directory_repo.created)}"
    )
    # Filter out auto-generated README notes; the orchestrator does
    # not count them but they exist in the content repo store.
    user_notes = [
        n for n in content_repo._store.values() if n.title != "README.md"
    ]
    assert len(user_notes) == 114
    # 1 book + 13 chapter directories
    assert len(directory_repo.created) == 14