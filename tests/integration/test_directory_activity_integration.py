"""Integration tests for ``DirectoryFacadeImpl.resolve_files_of_directory``.

The directory repo walks ``note.directory_subdirectory`` and
``note.directory_note`` instead of SpiceDB; the
``spicedb_permission_repo`` fixture still supplies the
``has_permission`` / ``lookup`` plumbing the facade needs for the
``view`` check on the root directory.
"""

import uuid

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from tests._fixtures_pkg.fakes import (
    _FakeDirectorySubdirectoryTable,
    _FakeDirectoryNoteTable,
    _FakeDirectoryTable,
    _FakeDirectoryTagsTable,
)
from src.api import ObjectRef, Relationship, SubjectRef
from src.db.repos.directory.directory import DirectoryFacadeImpl
from src.db.repos.directory.postgres import PostgresDirectoryRepo
from src.db.repos.permissions.permission import SpicedbPermissionRepo


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]


# Backwards-compatible alias: prior versions of this file declared
# ``permission_repo`` itself; route it through the canonical fixture.
@pytest.fixture(scope="function")
async def permission_repo(
    spicedb_permission_repo: SpicedbPermissionRepo,
) -> SpicedbPermissionRepo:
    """Alias around the canonical ``spicedb_permission_repo`` fixture."""
    return spicedb_permission_repo


async def test_resolve_files_of_directory_spicedb(
    spicedb_permission_repo: SpicedbPermissionRepo,
) -> None:
    user_id = "spicedb-user"
    root_id = f"root-{uuid.uuid4().hex}"
    child_id = f"child-{uuid.uuid4().hex}"
    note_root = f"note-root-{uuid.uuid4().hex}"
    note_child = f"note-child-{uuid.uuid4().hex}"

    relationships = [
        Relationship(
            resource=ObjectRef(object_type="directory", object_id=root_id),
            relation="reader",
            subject=SubjectRef(object_type="user", object_id=user_id),
        ),
    ]
    await spicedb_permission_repo.insert(relationships)

    subdirectory_table = _FakeDirectorySubdirectoryTable()
    subdirectory_table.add_directory_child(root_id, child_id)
    note_table = _FakeDirectoryNoteTable()
    note_table.add_note_child(root_id, note_root)
    note_table.add_note_child(child_id, note_child)

    directory_repo = DirectoryFacadeImpl(
        postgres_repo=PostgresDirectoryRepo(
            directory_table=_FakeDirectoryTable(),
            subdirectory_table=subdirectory_table,
            directory_note_table=note_table,
            directory_tags_table=_FakeDirectoryTagsTable(),
        ),
        permission_repo=spicedb_permission_repo,
    )

    resolved = await directory_repo.resolve_files_of_directory(
        directory_id=root_id,
        actor=UserContext(user_id=user_id),
        max_depth=3,
    )

    assert set(resolved) == {note_root, note_child}
