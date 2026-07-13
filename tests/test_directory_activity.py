from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from src.api.other.user_context import UserContextABC
from src.db.entities.note.versioning import NoteVersionEntry
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.db.repos.directory.directory import DirectoryFacadeImpl
from src.db.repos.directory.postgres import PostgresDirectoryRepo
from tests._fixtures_pkg.fakes import (
    _FakeDirectorySubdirectoryTable,
    _FakeDirectoryNoteTable,
    _FakeDirectoryTable,
    _FakeDirectoryTagsTable,
)
from tests.stubs import _UserContext
from src.api import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.services.directory_activity_service import DirectoryActivityServiceImpl
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from src.utils import logging_provider

from .fixtures import _FakeVersionRepo


class _FakeDirectoryRepo(DirectoryFacadeABC):
    def __init__(self, note_ids: List[str]) -> None:
        self._note_ids = note_ids

    async def create_directory(self, entity, user_ctx):  # type: ignore[override]
        raise NotImplementedError()

    async def fetch_directory(self, id: str, *, include=None):  # type: ignore[override]
        raise NotImplementedError()

    async def update_directory(self, entity):  # type: ignore[override]
        raise NotImplementedError()

    async def delete_directory(self, entity):  # type: ignore[override]
        raise NotImplementedError()

    async def add_note_to_directory(self, note_id: str, directory_id: str) -> None:
        raise NotImplementedError()

    async def remove_note_from_directory(self, note_id: str, directory_id: str) -> None:
        raise NotImplementedError()

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        return []

    async def list_note_directory_ids(self, note_id: str) -> List[str]:
        return []

    async def delete_directory(self, entity) -> bool:  # type: ignore[override]
        raise NotImplementedError()

    async def resolve_files_of_directory(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
    ) -> List[str]:
        return list(self._note_ids)

    async def resolve_subtree(
        self,
        directory_id: str,
        max_depth: int = 10,
    ) -> Tuple[List[str], List[str]]:
        return (list(self._note_ids), [directory_id])


async def test_resolve_files_of_directory_depth_and_cycle() -> None:
    """Depth + cycle correctness against the new hierarchy tables.

    The directory repo walks ``note.directory_subdirectory`` (for
    the directory tree) and ``note.directory_note`` (for the
    note bindings) instead of the SpiceDB relationships that
    used to drive this test, so the setup seeds rows directly.
    Visibility on the root directory is still routed through
    the in-memory permission repo so the ``has_permission``
    branch keeps its existing coverage.
    """
    permission_repo = InMemoryPermissionRepo()
    user_id = "alice"
    ctx = _UserContext(user_id)

    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "root"),
                relation=DirectoryRelationEnum.READER,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            ),
        ]
    )

    subdirectory_table = _FakeDirectorySubdirectoryTable()
    # child dirs under root
    subdirectory_table.add_directory_child("root", "child")
    subdirectory_table.add_directory_child("child", "grand")
    # cycle: grand is also a parent of root.  Add but expect it to be
    # ignored by the visited set.
    subdirectory_table.add_directory_child("grand", "root")
    note_table = _FakeDirectoryNoteTable()
    # notes at every level
    note_table.add_note_child("root", "note-root")
    note_table.add_note_child("child", "note-child")
    note_table.add_note_child("grand", "note-grand")

    directory_repo = DirectoryFacadeImpl(
        postgres_repo=PostgresDirectoryRepo(
            directory_table=_FakeDirectoryTable(),
            subdirectory_table=subdirectory_table,
            directory_note_table=note_table,
            directory_tags_table=_FakeDirectoryTagsTable(),
        ),
        permission_repo=permission_repo,
        log=logging_provider,
    )

    note_ids = await directory_repo.resolve_files_of_directory("root", ctx, max_depth=0)
    assert set(note_ids) == {"note-root"}

    note_ids = await directory_repo.resolve_files_of_directory("root", ctx, max_depth=1)
    assert set(note_ids) == {"note-root", "note-child"}

    note_ids = await directory_repo.resolve_files_of_directory("root", ctx, max_depth=2)
    assert set(note_ids) == {"note-root", "note-child", "note-grand"}

    with pytest.raises(ValueError):
        await directory_repo.resolve_files_of_directory("root", ctx, max_depth=-1)


async def test_directory_activity_orders_latest_changes() -> None:
    now = datetime(2026, 5, 20, 10, 0, 0)
    entries = {
        "note-1": NoteVersionEntry(
            version_id="v1",
            note_id="note-1",
            version_index=1,
            created_at=now,
            author_id="user-a",
            is_snapshot=True,
            snapshot_id="s1",
        ),
        "note-2": NoteVersionEntry(
            version_id="v2",
            note_id="note-2",
            version_index=2,
            created_at=now.replace(minute=5),
            author_id="user-b",
            is_snapshot=False,
            snapshot_id="s2",
        ),
    }

    version_repo = _FakeVersionRepo(entries)
    directory_repo = _FakeDirectoryRepo(["note-1", "note-2"])
    service = DirectoryActivityServiceImpl(
        version_repo=version_repo,
        directory_repo=directory_repo,
        log=logging_provider,
    )

    results = await service.list_directory_activity(
        directory_id="root",
        actor=_UserContext("user-a"),
        max_depth=3,
        limit=10,
        offset=0,
    )

    assert [entry.note_id for entry in results] == ["note-2", "note-1"]
