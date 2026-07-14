"""Unit tests for :meth:`DirectoryServiceImpl.dry_delete` and the
recursive :meth:`DirectoryServiceImpl.delete_directory` cascade.

Wires the real :class:`DirectoryServiceImpl` against the project-level
in-memory fakes (same pattern as
:file:`tests/test_thirdparty_migrations_real_services.py`) so the
``PermissionRepoABC.resolve_children`` -> :meth:`dry_delete` ->
:meth:`delete_directory` chain runs end-to-end without Postgres or
SpiceDB.  Pins:

* :meth:`dry_delete` returns every child directory / note /
  attachment enriched with id, kind and name, sorted by kind then id.
* :meth:`delete_directory` removes the root directory, every nested
  sub-directory (recursively), every note and every attachment --
  but only those that are exclusively owned by the subtree.
* A note that has another parent outside the subtree survives the
  cascade, and the same applies to attachments.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import pytest

from src.api.repos.permission_repo import (
    DirectoryChild,
    PermissionRepoABC,
)
from src.api.other.relationship import (
    AttachmentRelationEnum,
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.other.undefined import UNDEFINED
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.attachments.attachments import Attachment
from src.services.attachment_facade import AttachmentFacadeImpl
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from src.services.directory import DirectoryServiceImpl
from src.services.note import NoteServiceImpl
from src.db.table import TableABC
from tests._fixtures_pkg.fakes import (
    _FakeCombinedNoteRepo,
    _FakeDatabase,
    _FakeEmbeddingRepo,
    _FakeJwtProvider,
    _FakeNoteContentRepo,
    _FakeNoteRepoFacade,
    _FakeTagRepo,
    _FakeVersionRepo,
    _TestDirectoryRepo,
)
from tests.stubs.activity_logger_service import _FakeActivityLoggerService
from tests.stubs.attachments import (
    InMemoryAttachmentMetadataRepo,
    InMemoryAttachmentRepo,
)
from tests.stubs.logging import silent_logger
from tests.stubs.user_context import _UserContext as _UserCtx


class _FakeLinkTable(TableABC):
    """In-memory :class:`TableABC` for the ``note.attachment_note_link`` table."""

    def __init__(self) -> None:
        self.rows: List[dict] = []

    async def insert(self, record: dict) -> None:
        key = (record["note_id"], record["attachment_key"])
        if not any(
            (r["note_id"], r["attachment_key"]) == key for r in self.rows
        ):
            self.rows.append(record)

    async def delete(self, where: dict) -> None:
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


def _wire_service(
    *,
    user_id: str = "user-1",
    queue_note_ids: Optional[List[str]] = None,
) -> Tuple[
    DirectoryServiceImpl,
    _TestDirectoryRepo,
    _FakeNoteContentRepo,
    _FakeNoteRepoFacade,
    InMemoryAttachmentRepo,
    InMemoryAttachmentMetadataRepo,
    InMemoryPermissionRepo,
]:
    """Build a :class:`DirectoryServiceImpl` with the real ``NoteServiceImpl`` + ``AttachmentFacadeImpl``."""
    fake_db = _FakeDatabase()
    ids = queue_note_ids or [
        f"019f0000-0000-7000-8000-{i:012d}" for i in range(1, 50)
    ]
    for nid in ids:
        fake_db.fetchrow_responses.append({"id": nid})

    fake_facade = _FakeNoteRepoFacade()
    embedding_repo = _FakeEmbeddingRepo()
    permission_repo: PermissionRepoABC = InMemoryPermissionRepo()
    directory_repo = _TestDirectoryRepo(permission_repo=permission_repo)
    content_repo = _FakeNoteContentRepo(facade=fake_facade)
    jwt_provider = _FakeJwtProvider()
    activity_logger = _FakeActivityLoggerService()

    from src.db.repos.note.note_facade import NoteFacadeImpl

    real_facade = NoteFacadeImpl(
        db=fake_db,
        content_repo=content_repo,
        combined_repo=_FakeCombinedNoteRepo(content_repo=content_repo),
        embedding_repo=embedding_repo,
        logging_provider=lambda *_a, **_k: logging.getLogger(
            "test.directory.cascade"
        ),
        permission_repo=permission_repo,
        directory_repo=directory_repo,
        tag_repo=_FakeTagRepo(),
        version_repo=_FakeVersionRepo(),
    )

    # Bridge the real facade's insert into the fake facade's store
    # so ``update_note`` later can find the note by id.
    _real_insert = real_facade.insert

    async def _insert_bridge(note, user):
        result = await _real_insert(note, user)
        if result.note_id not in (None, UNDEFINED):
            content_repo.seed(result)
            fake_facade.notes_by_id[str(result.note_id)] = result
        return result

    real_facade.insert = _insert_bridge  # type: ignore[assignment]

    note_service = NoteServiceImpl(
        note_repo=real_facade,
        permission_repo=permission_repo,
        jwt_provider=jwt_provider,
        directory_repo=directory_repo,
        activity_logger=activity_logger,
        logging_provider=silent_logger,
    )

    attachment_repo = InMemoryAttachmentRepo()
    attachment_metadata_repo = InMemoryAttachmentMetadataRepo()
    attachment_facade = AttachmentFacadeImpl(
        attachment_repo=attachment_repo,
        metadata_repo=attachment_metadata_repo,
        permission_repo=permission_repo,
        attachments_note_link_table=_FakeLinkTable(),
        log=silent_logger,
    )

    directory_service = DirectoryServiceImpl(
        directory_repo=directory_repo,
        note_repo=real_facade,
        permission_repo=permission_repo,
        activity_logger=activity_logger,
        note_service=note_service,
        attachment_facade=attachment_facade,
        log=silent_logger,
    )
    return (
        directory_service,
        directory_repo,
        content_repo,
        fake_facade,
        attachment_repo,
        attachment_metadata_repo,
        permission_repo,
    )


async def _grant(
    perm_repo: PermissionRepoABC,
    user_id: str,
    *,
    directories: Optional[List[str]] = None,
    notes: Optional[List[str]] = None,
) -> None:
    """Grant the user ``admin`` on each directory and ``owner`` on each note."""
    rels: List[Relationship] = []
    for directory_id in directories or []:
        rels.append(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, directory_id),
                relation=DirectoryRelationEnum.ADMIN,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        )
    for note_id in notes or []:
        rels.append(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
                relation=NoteRelationEnum.OWNER,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        )
    if rels:
        await perm_repo.insert(rels)


async def _build_subtree(
    perm_repo: PermissionRepoABC,
    user_id: str,
    *,
    root: str,
    chapters: List[str],
    pages: List[
        Tuple[str, str]
    ],  # (note_id, parent_directory_id; "root" means the root dir)
    attachments: List[Tuple[str, str]],  # (attachment_key, note_id)
) -> None:
    """Plant relations for a small book-shaped subtree.

    Also grants the test user ``admin`` on every directory and
    ``owner`` on every note so the cascade's permission checks
    pass.
    """
    rels: List[Relationship] = []
    for chapter_id in chapters:
        rels.append(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, chapter_id),
                relation=DirectoryRelationEnum.PARENT,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, root),
            )
        )
        rels.append(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, chapter_id),
                relation=DirectoryRelationEnum.ADMIN,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        )
    for note_id, parent_id in pages:
        rels.append(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, parent_id),
            )
        )
        rels.append(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
                relation=NoteRelationEnum.OWNER,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        )
    for attachment_key, note_id in attachments:
        rels.append(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                relation=AttachmentRelationEnum.PARENT_NOTE,
                subject=SubjectRef(ObjectTypeEnum.NOTE, note_id),
            )
        )
        rels.append(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                relation=AttachmentRelationEnum.WRITE,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        )
        rels.append(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                relation=AttachmentRelationEnum.VIEW,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        )
    if rels:
        await perm_repo.insert(rels)


def _seed_directory(directory_repo: _TestDirectoryRepo, id_: str, name: str) -> None:
    directory_repo.directories_by_id[id_] = DirectoryEntity(
        id=id_, slug=name, display_name=name
    )


def _seed_note(content_repo: _FakeNoteContentRepo, id_: str, title: str) -> None:
    content_repo._store[id_] = NoteEntity(note_id=id_, title=title)


# ---------------------------------------------------------------------------
# dry_delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_delete_returns_empty_list_when_subtree_is_empty() -> None:
    (
        directory_service,
        _directory_repo,
        _content_repo,
        _note_facade,
        _attachment_repo,
        _metadata_repo,
        perm_repo,
    ) = _wire_service()
    user_ctx = _UserCtx(user_id="user-1")
    await _grant(perm_repo, "user-1", directories=["dir-1"])

    result = await directory_service.dry_delete("dir-1", user_ctx)

    # ``dry_delete`` excludes the root itself from its output.
    assert result == []


@pytest.mark.asyncio
async def test_dry_delete_returns_subtree_with_kind_and_name() -> None:
    (
        directory_service,
        directory_repo,
        content_repo,
        _note_facade,
        _attachment_repo,
        metadata_repo,
        perm_repo,
    ) = _wire_service()
    user_ctx = _UserCtx(user_id="user-1")
    await _grant(perm_repo, "user-1", directories=["dir-1"])
    await _build_subtree(
        perm_repo,
        user_id="user-1",
        root="dir-1",
        chapters=["dir-2"],
        # note-1 in dir-2 (chapter_id=2); note-2 directly in dir-1 (chapter_id=0)
        pages=[("note-1", "dir-2"), ("note-2", "dir-1")],
        attachments=[("att-1", "note-1")],
    )

    _seed_directory(directory_repo, "dir-2", "Chapter 1")
    _seed_directory(directory_repo, "dir-1", "Book")
    _seed_note(content_repo, "note-1", "Page A")
    _seed_note(content_repo, "note-2", "Top page")
    metadata_repo._metadata["att-1"] = Attachment(key="att-1", filename="cover.png")

    result = await directory_service.dry_delete("dir-1", user_ctx)

    by_id = {c.id: c for c in result}
    assert [c.kind for c in result] == ["directory", "note", "note", "attachment"]
    assert by_id["dir-2"] == DirectoryChild(
        id="dir-2", kind="directory", name="Chapter 1"
    )
    assert by_id["note-1"] == DirectoryChild(
        id="note-1", kind="note", name="Page A"
    )
    assert by_id["note-2"] == DirectoryChild(
        id="note-2", kind="note", name="Top page"
    )
    assert by_id["att-1"] == DirectoryChild(
        id="att-1", kind="attachment", name="cover.png"
    )


@pytest.mark.asyncio
async def test_dry_delete_skips_notes_with_parents_outside_subtree() -> None:
    """A note shared with an outside directory is not in the dry-delete list."""
    (
        directory_service,
        directory_repo,
        content_repo,
        _note_facade,
        _attachment_repo,
        _metadata_repo,
        perm_repo,
    ) = _wire_service()
    user_ctx = _UserCtx(user_id="user-1")
    await _grant(perm_repo, "user-1", directories=["dir-1", "outside"])
    await perm_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "exclusive"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "shared"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "shared"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "outside"),
            ),
        ]
    )
    _seed_directory(directory_repo, "dir-1", "Book")
    _seed_note(content_repo, "exclusive", "Exclusive")
    _seed_note(content_repo, "shared", "Shared")

    result = await directory_service.dry_delete("dir-1", user_ctx)

    by_id = {c.id: c for c in result}
    assert "exclusive" in by_id
    assert "shared" not in by_id


# ---------------------------------------------------------------------------
# delete_directory cascade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_directory_cascades_into_subdirectories_notes_and_attachments() -> None:
    (
        directory_service,
        directory_repo,
        content_repo,
        _note_facade,
        attachment_repo,
        metadata_repo,
        perm_repo,
    ) = _wire_service()
    user_ctx = _UserCtx(user_id="user-1")
    await _grant(perm_repo, "user-1", directories=["dir-1"])
    await _build_subtree(
        perm_repo,
        user_id="user-1",
        root="dir-1",
        chapters=["dir-2"],
        pages=[("note-1", "dir-2"), ("note-2", "dir-1")],
        attachments=[("att-1", "note-1"), ("att-2", "note-2")],
    )

    _seed_directory(directory_repo, "dir-1", "Book")
    _seed_directory(directory_repo, "dir-2", "Chapter 1")
    _seed_note(content_repo, "note-1", "Page A")
    _seed_note(content_repo, "note-2", "Top")
    metadata_repo._metadata["att-1"] = Attachment(key="att-1", filename="a.png")
    metadata_repo._metadata["att-2"] = Attachment(key="att-2", filename="b.png")
    attachment_repo._store["att-1"] = b"a"
    attachment_repo._store["att-2"] = b"b"

    deleted = await directory_service.delete_directory("dir-1", user_ctx)

    assert deleted is True
    assert "dir-1" not in directory_repo.directories_by_id
    assert "dir-2" not in directory_repo.directories_by_id
    assert "note-1" not in content_repo._store
    assert "note-2" not in content_repo._store
    assert "att-1" not in metadata_repo._metadata
    assert "att-2" not in metadata_repo._metadata
    assert "att-1" not in attachment_repo._store
    assert "att-2" not in attachment_repo._store


@pytest.mark.asyncio
async def test_delete_directory_keeps_shared_note_alone() -> None:
    """A note that has another parent outside the subtree survives."""
    (
        directory_service,
        directory_repo,
        content_repo,
        _note_facade,
        _attachment_repo,
        _metadata_repo,
        perm_repo,
    ) = _wire_service()
    user_ctx = _UserCtx(user_id="user-1")
    await _grant(perm_repo, "user-1", directories=["dir-1", "outside"])
    await perm_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "shared"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "shared"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "outside"),
            ),
        ]
    )
    _seed_directory(directory_repo, "dir-1", "Book")
    _seed_note(content_repo, "shared", "Shared")

    deleted = await directory_service.delete_directory("dir-1", user_ctx)

    assert deleted is True
    assert "dir-1" not in directory_repo.directories_by_id
    assert "shared" in content_repo._store


@pytest.mark.asyncio
async def test_delete_directory_does_not_recurse_when_no_children() -> None:
    """Deleting a leaf directory is the simple path."""
    (
        directory_service,
        directory_repo,
        _content_repo,
        _note_facade,
        _attachment_repo,
        _metadata_repo,
        perm_repo,
    ) = _wire_service()
    user_ctx = _UserCtx(user_id="user-1")
    await _grant(perm_repo, "user-1", directories=["dir-1"])

    _seed_directory(directory_repo, "dir-1", "Lonely")

    assert await directory_service.delete_directory("dir-1", user_ctx) is True
    assert "dir-1" not in directory_repo.directories_by_id


@pytest.mark.asyncio
async def test_dry_delete_requires_view_permission() -> None:
    """``dry_delete`` gates on view, not delete, since it does not mutate."""
    (
        directory_service,
        _directory_repo,
        _content_repo,
        _note_facade,
        _attachment_repo,
        _metadata_repo,
        _perm_repo,
    ) = _wire_service()
    user_ctx = _UserCtx(user_id="user-1")

    with pytest.raises(PermissionError):
        await directory_service.dry_delete("dir-1", user_ctx)


@pytest.mark.asyncio
async def test_delete_directory_requires_delete_permission() -> None:
    """``delete_directory`` gates on the delete chain."""
    (
        directory_service,
        _directory_repo,
        _content_repo,
        _note_facade,
        _attachment_repo,
        _metadata_repo,
        perm_repo,
    ) = _wire_service()
    user_ctx = _UserCtx(user_id="user-1")
    await perm_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
                relation=DirectoryRelationEnum.READER,
                subject=SubjectRef(ObjectTypeEnum.USER, "user-1"),
            )
        ]
    )

    with pytest.raises(PermissionError):
        await directory_service.delete_directory("dir-1", user_ctx)