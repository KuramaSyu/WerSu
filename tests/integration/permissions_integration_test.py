"""Integration tests for :class:`NotePermissionRepoSpicedb`.

The ``spicedb_permission_repo`` fixture (from ``tests/fixtures/spicedb.py``)
spins up a real SpiceDB container once per test and applies the canonical
schema.  The two tests below exercise the production-style ``insert`` +
``lookup`` + ``has_permission`` + ``get_permissions`` path against it.
"""

from typing import AsyncIterator

import pytest

import uuid
from tests.stubs.user_context import _UserContext as UserContext
from src.api import ObjectRef, Relationship, SubjectRef
from src.db.repos.permissions.permission import SpicedbPermissionRepo


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]


# Local alias kept for backwards compatibility with the
# ``async def test_note_insert_and_check(note_permissions_repo)``
# signatures that pre-date the fixture rename.
@pytest.fixture(scope="function")
async def note_permissions_repo(
    spicedb_permission_repo: AsyncIterator[SpicedbPermissionRepo],
) -> AsyncIterator[SpicedbPermissionRepo]:
    """Alias around the canonical ``spicedb_permission_repo`` fixture.

    Kept here so existing test bodies keep using
    ``note_permissions_repo`` even though the new fixture is named
    ``spicedb_permission_repo``.
    """
    async for repo in spicedb_permission_repo:
        yield repo


async def test_note_insert_and_check(spicedb_permission_repo: SpicedbPermissionRepo):
    emilia = SubjectRef(object_type="user", object_id="emilia")
    alfred = SubjectRef(object_type="user", object_id="alfred")

    note_id = f"note-{uuid.uuid4().hex}"
    note = ObjectRef(object_type="note", object_id=note_id)

    relationships = [
        Relationship(resource=note, relation="admin", subject=emilia),
        Relationship(resource=note, relation="reader", subject=alfred),
    ]

    inserted = await spicedb_permission_repo.insert(relationships)
    assert len(inserted) == 2

    emilia_notes = await spicedb_permission_repo.lookup_notes(UserContext("emilia"), "view")
    assert [obj.object_id for obj in emilia_notes] == [note_id]

    alfred_notes = await spicedb_permission_repo.lookup_notes(UserContext("alfred"), "view")
    assert [obj.object_id for obj in alfred_notes] == [note_id]

    alfred_admin_notes = await spicedb_permission_repo.lookup_notes(
        UserContext("alfred"), "admin"
    )
    assert [obj.object_id for obj in alfred_admin_notes] == []


async def test_note_missing_permissions(spicedb_permission_repo: SpicedbPermissionRepo):
    emilia = SubjectRef(object_type="user", object_id="emilia")
    alfred = SubjectRef(object_type="user", object_id="alfred")

    reader_note_id = f"note-{uuid.uuid4().hex}"
    admin_note_id = f"note-{uuid.uuid4().hex}"
    reader_note = ObjectRef(object_type="note", object_id=reader_note_id)
    admin_note = ObjectRef(object_type="note", object_id=admin_note_id)

    inserted = await spicedb_permission_repo.insert(
        [
            Relationship(resource=reader_note, relation="reader", subject=emilia),
            Relationship(resource=admin_note, relation="admin", subject=alfred),
        ]
    )
    assert len(inserted) == 2

    # Emilia can view her reader note, but is not admin there.
    assert await spicedb_permission_repo.has_permission(
        UserContext("emilia"), "view", reader_note
    )
    assert not await spicedb_permission_repo.has_permission(
        UserContext("emilia"), "admin", reader_note
    )

    # Emilia has no permissions at all on Alfred's note.
    assert not await spicedb_permission_repo.has_permission(
        UserContext("emilia"), "view", admin_note
    )
    assert not await spicedb_permission_repo.has_permission(
        UserContext("emilia"), "admin", admin_note
    )
    assert await spicedb_permission_repo.get_permissions(
        UserContext("emilia"), admin_note
    ) == []
