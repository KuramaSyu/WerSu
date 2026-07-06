"""Unit tests for :class:`src.services.directory.DirectoryService`.

These tests reuse the shared test doubles from
:mod:`tests._fixtures_pkg.fakes` and :mod:`tests.db.repos.permissions`
so the suite does not need Postgres or SpiceDB.  The
:class:`~tests._fixtures_pkg.fakes._FakeNoteRepoFacade` covers the
note-repo surface and the canonical in-memory
:class:`~src.db.repos.permissions.permission.NotePermissionRepoInMemory`
covers permissions.

Coverage:

* Permission chain wiring for every public method.
* :meth:`DirectoryService.get_directory_notes` -- README pinning at
  offset 0, README auto-creation, and pagination.
"""

from __future__ import annotations

from datetime import datetime

from tests.stubs.user_context import _UserContext
from src.api.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.directory.directory import DirectoryEntity
from src.db.repos.permissions.permission import NotePermissionRepoInMemory
from src.services.directory import DirectoryService, README_TITLE
from tests._fixtures_pkg.fakes import _FakeNoteRepoFacade, _TestDirectoryRepo
from tests.stubs.activity_logger_service import _FakeActivityLoggerService


def _make_service() -> tuple[
    DirectoryService,
    _TestDirectoryRepo,
    _FakeNoteRepoFacade,
    NotePermissionRepoInMemory,
    _FakeActivityLoggerService,
]:
    directory_repo = _TestDirectoryRepo()
    note_repo = _FakeNoteRepoFacade()
    permission_repo = NotePermissionRepoInMemory()
    activity_logger = _FakeActivityLoggerService()
    service = DirectoryService(
        directory_repo=directory_repo,
        note_repo=note_repo,
        permission_repo=permission_repo,
        activity_logger=activity_logger,
    )
    return service, directory_repo, note_repo, permission_repo, activity_logger


async def _grant_view(permission_repo: NotePermissionRepoInMemory, user_id: str, directory_id: str) -> None:
    """Grant the user `view` on `directory_id` via the in-memory permission repo."""
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, directory_id),
                relation=DirectoryRelationEnum.READER,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        ]
    )


async def test_get_directory_notes_creates_readme_when_missing() -> None:
    service, _, note_repo, permission_repo, _activity_logger = _make_service()
    await _grant_view(permission_repo, "user-1", "dir-1")

    note_repo.notes_by_id["note-99"] = NoteEntity(
        note_id="note-99",
        title="unrelated",
        author_id="user-1",
        content="",
        updated_at=datetime.now(),
        embeddings=[],
        permissions=[],
    )
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-99"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            )
        ]
    )

    notes = await service.get_directory_notes(
        "dir-1", _UserContext("user-1"), limit=10, offset=0
    )

    assert len(note_repo.insert_calls) == 1
    created = note_repo.insert_calls[0]
    assert created.title == README_TITLE
    assert created.parent_dir_id == "dir-1"
    assert len(notes) == 2
    assert notes[0].title == README_TITLE


async def test_get_directory_notes_returns_existing_readme_at_offset_zero() -> None:
    service, _, note_repo, permission_repo, _activity_logger = _make_service()
    await _grant_view(permission_repo, "user-1", "dir-1")

    note_repo.notes_by_id["note-1"] = NoteEntity(
        note_id="note-1",
        title=README_TITLE,
        author_id="user-1",
        content="hello",
        updated_at=datetime.now(),
        embeddings=[],
        permissions=[],
    )
    note_repo.notes_by_id["note-2"] = NoteEntity(
        note_id="note-2",
        title="Other",
        author_id="user-1",
        content="",
        updated_at=datetime.now(),
        embeddings=[],
        permissions=[],
    )
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-2"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
        ]
    )

    notes = await service.get_directory_notes(
        "dir-1", _UserContext("user-1"), limit=10, offset=0
    )

    assert [n.note_id for n in notes] == ["note-1", "note-2"]
    assert note_repo.insert_calls == []


async def test_get_directory_notes_paginates_with_offset() -> None:
    service, _, note_repo, permission_repo, _activity_logger = _make_service()
    await _grant_view(permission_repo, "user-1", "dir-1")

    rels = []
    for i in range(5):
        nid = f"note-{i}"
        note_repo.notes_by_id[nid] = NoteEntity(
            note_id=nid,
            title=f"note-{i}",
            author_id="user-1",
            content="",
            updated_at=datetime.now(),
            embeddings=[],
            permissions=[],
        )
        rels.append(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, nid),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            )
        )
    note_repo.notes_by_id["readme-id"] = NoteEntity(
        note_id="readme-id",
        title=README_TITLE,
        author_id="user-1",
        content="",
        updated_at=datetime.now(),
        embeddings=[],
        permissions=[],
    )
    rels.append(
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, "readme-id"),
            relation=NoteRelationEnum.PARENT_DIRECTORY,
            subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
        )
    )
    await permission_repo.insert(rels)

    page0 = await service.get_directory_notes(
        "dir-1", _UserContext("user-1"), limit=2, offset=0
    )
    page1 = await service.get_directory_notes(
        "dir-1", _UserContext("user-1"), limit=2, offset=2
    )

    assert [n.note_id for n in page0] == ["readme-id", "note-0"]
    # offset 2 skips the first 2 elements (README + note-0)
    assert [n.note_id for n in page1] == ["note-1", "note-2"]


async def test_patch_directory_requires_write_permission() -> None:
    service, _, _, _, _ = _make_service()

    try:
        await service.patch_directory(
            DirectoryEntity(id="dir-1", name="x"),
            _UserContext("user-1"),
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("expected PermissionError for unknown directory")


async def test_delete_directory_requires_delete_permission() -> None:
    service, _, _, _, _ = _make_service()

    try:
        await service.delete_directory("dir-1", _UserContext("user-1"))
    except PermissionError:
        pass
    else:
        raise AssertionError("expected PermissionError for unknown directory")


async def test_create_directory_grants_caller_admin_relation() -> None:
    service, directory_repo, _, _, _ = _make_service()

    await service.create_directory(
        DirectoryEntity(name="root"),
        _UserContext("user-1"),
    )

    assert len(directory_repo.created) == 1
    created = directory_repo.created[0]
    assert isinstance(created.relations, list)
    admin_rel = created.relations[0]
    assert admin_rel.relation == DirectoryRelationEnum.ADMIN
    assert admin_rel.subject.object_id == "user-1"


async def test_create_directory_binds_readme_note_when_not_supplied() -> None:
    """A fresh directory auto-creates a ``README.md`` and binds its id."""
    service, directory_repo, note_repo, permission_repo, _activity_logger = _make_service()

    await _grant_view(permission_repo, "user-1", "root-id")

    created = await service.create_directory(
        DirectoryEntity(name="root"),
        _UserContext("user-1"),
    )

    # one README note was inserted during create_directory
    assert len(note_repo.insert_calls) == 1
    readme = note_repo.insert_calls[0]
    assert readme.title == README_TITLE
    assert readme.parent_dir_id == created.id

    # the in-memory directory repo received an update with the bound id
    bound = [
        u for u in directory_repo.updated
        if u.id is not None and str(u.id) == str(created.id)
        and u.readme_note_id is not UNDEFINED
    ]
    assert bound, "expected update_directory to set readme_note_id"
    assert str(bound[-1].readme_note_id) == str(readme.note_id)

    # the returned entity carries the same binding
    assert created.readme_note_id is not UNDEFINED
    assert str(created.readme_note_id) == str(readme.note_id)

    # ...and the matching `note#parent_directory@directory` relation exists
    parent_dir_rels = [
        rel
        for rel in permission_repo._store  # type: ignore[attr-defined]
        if str(rel.relation) == str(NoteRelationEnum.PARENT_DIRECTORY)
        and str(rel.subject.object_type) == str(ObjectTypeEnum.DIRECTORY)
        and str(rel.resource.object_id) == str(readme.note_id)
    ]
    assert parent_dir_rels, "expected parent_directory relation for auto README"


async def test_create_directory_does_not_overwrite_supplied_readme_note_id() -> None:
    """Pre-set `readme_note_id` survives `create_directory` unchanged."""
    service, directory_repo, note_repo, permission_repo, _activity_logger = _make_service()

    created = await service.create_directory(
        DirectoryEntity(name="root", readme_note_id="preset-readme"),
        _UserContext("user-1"),
    )

    assert note_repo.insert_calls == []
    assert str(created.readme_note_id) == "preset-readme"
    # the preset path must still write the parent-directory relation
    parent_dir_rels = [
        rel
        for rel in permission_repo._store  # type: ignore[attr-defined]
        if str(rel.relation) == str(NoteRelationEnum.PARENT_DIRECTORY)
        and str(rel.subject.object_type) == str(ObjectTypeEnum.DIRECTORY)
        and str(rel.resource.object_id) == "preset-readme"
    ]
    assert parent_dir_rels, "expected parent_directory relation for preset README"


# ---------------------------------------------------------------------------
# activity logging
# ---------------------------------------------------------------------------


async def test_get_directory_records_directory_viewed() -> None:
    """`get_directory` records `directory_viewed` after a successful fetch."""
    service, directory_repo, _, permission_repo, activity_logger = _make_service()
    directory_repo.directories_by_id["dir-1"] = DirectoryEntity(
        id="dir-1", name="root"
    )
    await _grant_view(permission_repo, "user-1", "dir-1")

    await service.get_directory("dir-1", _UserContext("user-1"))

    assert activity_logger.calls == [
        ("directory_viewed", "dir-1", "user-1", {})
    ]


async def test_get_directory_does_not_record_on_miss() -> None:
    """`get_directory` records nothing when the permission check denies the actor."""
    service, directory_repo, _, _, activity_logger = _make_service()
    directory_repo.directories_by_id.clear()

    try:
        await service.get_directory("missing", _UserContext("user-1"))
    except PermissionError:
        pass

    assert activity_logger.calls == []

# currently disabled on purpose
# async def test_get_directories_records_directory_viewed_per_directory() -> None:
#     """`get_directories` records `directory_viewed` for every directory returned."""
#     service, directory_repo, _, permission_repo, activity_logger = _make_service()
#     directory_repo.user_to_directory_ids["user-1"] = ["dir-1", "dir-2"]
#     directory_repo.directories_by_id["dir-1"] = DirectoryEntity(id="dir-1", name="a")
#     directory_repo.directories_by_id["dir-2"] = DirectoryEntity(id="dir-2", name="b")
#     await permission_repo.insert(
#         [
#             Relationship(
#                 resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
#                 relation=DirectoryRelationEnum.READER,
#                 subject=SubjectRef(ObjectTypeEnum.USER, "user-1"),
#             ),
#             Relationship(
#                 resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
#                 relation=DirectoryRelationEnum.READER,
#                 subject=SubjectRef(ObjectTypeEnum.USER, "user-1"),
#             ),
#         ]
#     )

#     await service.get_directories(_UserContext("user-1"))

#     assert activity_logger.calls == [
#         ("directory_viewed", "dir-1", "user-1", {}),
#         ("directory_viewed", "dir-2", "user-1", {}),
#     ]


async def test_create_directory_records_directory_created() -> None:
    """`create_directory` records `directory_created` on success."""
    service, _, _, _, activity_logger = _make_service()

    created = await service.create_directory(
        DirectoryEntity(name="root"),
        _UserContext("user-1"),
    )

    assert ("directory_created", str(created.id), "user-1", {}) in activity_logger.calls


async def test_patch_directory_records_directory_edited() -> None:
    """`patch_directory` records `directory_edited` when the repo updates a row."""
    service, directory_repo, _, permission_repo, activity_logger = _make_service()
    directory_repo.directories_by_id["dir-1"] = DirectoryEntity(
        id="dir-1", name="root"
    )
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
                relation=DirectoryRelationEnum.WRITER,
                subject=SubjectRef(ObjectTypeEnum.USER, "user-1"),
            )
        ]
    )

    await service.patch_directory(
        DirectoryEntity(id="dir-1", name="renamed"),
        _UserContext("user-1"),
    )

    assert ("directory_edited", "dir-1", "user-1", {}) in activity_logger.calls


async def test_delete_directory_records_directory_deleted() -> None:
    """`delete_directory` records `directory_deleted` on a successful delete."""
    service, directory_repo, _, permission_repo, activity_logger = _make_service()
    directory_repo.directories_by_id["dir-1"] = DirectoryEntity(
        id="dir-1", name="root"
    )
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
                relation=DirectoryRelationEnum.ADMIN,
                subject=SubjectRef(ObjectTypeEnum.USER, "user-1"),
            )
        ]
    )

    deleted = await service.delete_directory("dir-1", _UserContext("user-1"))

    assert deleted is True
    assert ("directory_deleted", "dir-1", "user-1", {}) in activity_logger.calls


async def test_delete_directory_does_not_record_on_permission_denied() -> None:
    """`delete_directory` records nothing when the actor lacks the admin permission."""
    service, directory_repo, _, _, activity_logger = _make_service()
    directory_repo.directories_by_id["dir-1"] = DirectoryEntity(
        id="dir-1", name="root"
    )

    try:
        await service.delete_directory("dir-1", _UserContext("user-1"))
    except PermissionError:
        pass

    assert activity_logger.calls == []
