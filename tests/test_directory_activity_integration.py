"""Integration tests for ``DirectoryRepoSpicedbPostgres.resolve_files_of_directory``.

The ``spicedb_permission_repo`` fixture (from ``tests/fixtures/spicedb.py``)
spins up a fresh SpiceDB container per test; the test then wires a
``DirectoryRepoSpicedbPostgres`` against it and exercises the
graph-walk that resolves transitive parent/child membership.
"""

import uuid

import pytest

from src.api import ObjectRef, Relationship, SubjectRef
from src.db.repos.directory.directory import DirectoryRepoSpicedbPostgres
from src.db.repos.note.note import UserContext
from src.db.repos.permissions.permission import NotePermissionRepoSpicedb


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]


# Backwards-compatible alias: prior versions of this file declared
# ``permission_repo`` itself; route it through the canonical fixture.
@pytest.fixture(scope="function")
async def permission_repo(
    spicedb_permission_repo: NotePermissionRepoSpicedb,
) -> NotePermissionRepoSpicedb:
    """Alias around the canonical ``spicedb_permission_repo`` fixture."""
    return spicedb_permission_repo


async def test_resolve_files_of_directory_spicedb(
    spicedb_permission_repo: NotePermissionRepoSpicedb,
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
        Relationship(
            resource=ObjectRef(object_type="directory", object_id=child_id),
            relation="parent",
            subject=SubjectRef(object_type="directory", object_id=root_id),
        ),
        Relationship(
            resource=ObjectRef(object_type="note", object_id=note_root),
            relation="parent_directory",
            subject=SubjectRef(object_type="directory", object_id=root_id),
        ),
        Relationship(
            resource=ObjectRef(object_type="note", object_id=note_child),
            relation="parent_directory",
            subject=SubjectRef(object_type="directory", object_id=child_id),
        ),
    ]
    await spicedb_permission_repo.insert(relationships)

    directory_repo = DirectoryRepoSpicedbPostgres(
        db=None,  # type: ignore[arg-type]
        permission_repo=spicedb_permission_repo,
        spicedb_client=spicedb_permission_repo.client,
    )

    resolved = await directory_repo.resolve_files_of_directory(
        directory_id=root_id,
        actor=UserContext(user_id=user_id),
        max_depth=3,
    )

    assert set(resolved) == {note_root, note_child}
