"""Integration test coverage for ``DefaultSharingService`` with real infrastructure.

These tests exercise the share service end-to-end against real Postgres
and SpiceDB containers, validating that:

1. creating a share persists a row in ``shared`` and a temporary user
   in ``users``, and grants the right SpiceDB permission to the access
   user;
2. updating a share's permission swaps the access user's SpiceDB
   relation from reader to writer (or vice versa);
3. deleting a share removes the row, deletes the temporary user, and
   clears the access user's SpiceDB relations on the note.

The tests are marked ``integration`` and ``spicedb`` and are excluded
from the default test run configured in ``pytest.ini``.
"""

from datetime import datetime
from typing import Tuple

import pytest

from src.api import ObjectRef, ObjectTypeEnum
from src.api.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.sharing import NoteShareEntity
from src.db.repos.note.note import UserContext
from src.db.repos.sharing.sharing import SharingPostgresRepo
from src.services.sharing import DefaultSharingService
from tests.integration_helpers import (
    NoteRelationEnum,
    make_user_entity,
    sharing_service_env,
    wait_until,
)


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]


EnvT = Tuple[DefaultSharingService, SharingPostgresRepo]

# TODO: Remove what for and setup repo with consistency=true 

async def _bootstrap_owner_with_note(env) -> Tuple[str, str]:
    """Create an owner user and a note, return ``(user_id, note_id)``.

    The owner becomes admin on all default directories and on the new
    note, so they have the ``edit_permissions`` capability required by
    the share service to mutate shares.
    """
    created_user = await env.user_service.create_user(
        make_user_entity(
            discord_id=99887766,
            username="share-owner",
            discriminator="0001",
            email="share-owner@example.com",
        )
    )
    if created_user.id is None:
        pytest.fail(f"create_user() returned a user without an ID: {created_user!r}")
    user_id = str(created_user.id)

    note = await env.note_repo.insert(
        NoteEntity(
            title="share-target",
            content="",
            updated_at=datetime.now(),
            author_id=created_user.id,
        ),
        UserContext(user_id),
    )
    if note.note_id is None:
        pytest.fail(f"note_repo.insert() returned a note without an ID: {note!r}")
    return user_id, str(note.note_id)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

async def test_create_share_persists_row_and_grants_access(
    sharing_service_env,
) -> None:
    """Creating a share writes the row, creates the access user, and grants read."""
    env = sharing_service_env
    sharing_service: DefaultSharingService = env.sharing_service

    user_id, note_id = await _bootstrap_owner_with_note(env)

    created = await sharing_service.create_share(
        NoteShareEntity(
            note_id=note_id,
            description="first share",
            permission="read",
        ),
        UserContext(user_id),
    )

    if created.id is UNDEFINED or created.id is None:
        pytest.fail(f"create_share() did not assign a share ID: {created!r}")
    if created.access_as is UNDEFINED or created.access_as is None:
        pytest.fail(f"create_share() did not assign an access_as user: {created!r}")
    share_id = str(created.id)
    access_as = str(created.access_as)

    # 1. Share row is in Postgres with audit fields populated.
    fetched = await env.sharing_repo.get_share_by_id(share_id, UserContext(user_id))
    assert fetched.id == share_id, (
        f"share row not retrievable by id; got {fetched!r}"
    )
    assert fetched.note_id == note_id, (
        f"share note_id mismatch: expected {note_id!r}, got {fetched.note_id!r}"
    )
    assert fetched.created_by == user_id, (
        f"share created_by mismatch: expected {user_id!r}, got {fetched.created_by!r}"
    )
    assert fetched.description == "first share"

    # 2. Access user is a real user in Postgres.
    access_user = await env.user_repo.select(access_as)
    if access_user is None:
        pytest.fail(f"access user {access_as!r} was not persisted in users table")
    assert access_user.username, "access user must have a generated username"

    # 3. Access user has `reader` permission on the shared note in SpiceDB.
    resource = ObjectRef(ObjectTypeEnum.NOTE, note_id)

    async def _can_view() -> bool:
        return await env.permission_repo.has_permission(
            UserContext(access_as), "view", resource
        )

    await wait_until(
        _can_view,
        description=f"access user {access_as!r} has 'view' on note {note_id!r}",
    )
    assert not await env.permission_repo.has_permission(
        UserContext(access_as), "write", resource
    ), (
        f"access user {access_as!r} unexpectedly has 'write' on note {note_id!r} "
        f"after a read-only share was created"
    )


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

async def test_update_share_permission_swaps_reader_to_writer(
    sharing_service_env,
) -> None:
    """Updating a share's permission replaces the access user's SpiceDB relation."""
    env = sharing_service_env
    sharing_service: DefaultSharingService = env.sharing_service

    user_id, note_id = await _bootstrap_owner_with_note(env)

    created = await sharing_service.create_share(
        NoteShareEntity(note_id=note_id, permission="read"),
        UserContext(user_id),
    )
    if created.id is UNDEFINED or created.access_as is None or created.access_as is UNDEFINED:
        pytest.fail(f"create_share() returned an incomplete share: {created!r}")
    share_id = str(created.id)
    access_as = str(created.access_as)

    resource = ObjectRef(ObjectTypeEnum.NOTE, note_id)

    # Wait for the initial read relation to become visible.
    async def _can_view() -> bool:
        return await env.permission_repo.has_permission(
            UserContext(access_as), "view", resource
        )

    await wait_until(_can_view, description=f"initial 'view' grant for {access_as!r}")

    # Flip the share to "write".
    updated = await sharing_service.update_share(
        NoteShareEntity(id=share_id, permission="write"),
        UserContext(user_id),
    )
    if updated.id is None or str(updated.id) != share_id:
        pytest.fail(
            f"update_share() returned a different share: expected id={share_id!r}, "
            f"got {updated!r}"
        )
    # `permission` lives in SpiceDB, not in the Postgres row, so the
    # returned entity may have UNDEFINED here.  The SpiceDB-level
    # assertions below verify that the relation was actually swapped.

    # The access user should now have write, but no longer be a plain reader.
    async def _can_write() -> bool:
        return await env.permission_repo.has_permission(
            UserContext(access_as), "write", resource
        )

    await wait_until(
        _can_write,
        description=f"access user {access_as!r} has 'write' on note {note_id!r} "
        f"after share update",
    )

    # The direct-relation check is also eventually consistent: poll until
    # the access user's reader tuple has been replaced by a writer tuple.
    async def _direct_relations_swapped() -> bool:
        direct = await env.permission_repo.list_relationships(resource)
        access_user_relations = [
            rel
            for rel in direct
            if str(rel.subject.object_type) == ObjectTypeEnum.USER
            and str(rel.subject.object_id) == access_as
        ]
        relations_as_set = {str(rel.relation) for rel in access_user_relations}
        return (
            NoteRelationEnum.WRITER.value in relations_as_set
            and NoteRelationEnum.READER.value not in relations_as_set
        )

    await wait_until(
        _direct_relations_swapped,
        description=f"reader tuple replaced by writer for {access_as!r} on {note_id!r}",
    )


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

async def test_delete_share_removes_row_user_and_relations(
    sharing_service_env,
) -> None:
    """Deleting a share tears down row, temp user, and SpiceDB relations."""
    env = sharing_service_env
    sharing_service: DefaultSharingService = env.sharing_service

    user_id, note_id = await _bootstrap_owner_with_note(env)

    created = await sharing_service.create_share(
        NoteShareEntity(note_id=note_id, permission="write"),
        UserContext(user_id),
    )
    if created.id is UNDEFINED or created.access_as is None or created.access_as is UNDEFINED:
        pytest.fail(f"create_share() returned an incomplete share: {created!r}")
    share_id = str(created.id)
    access_as = str(created.access_as)

    resource = ObjectRef(ObjectTypeEnum.NOTE, note_id)

    # Confirm the write relation landed before we attempt to delete.
    async def _can_write() -> bool:
        return await env.permission_repo.has_permission(
            UserContext(access_as), "write", resource
        )

    await wait_until(_can_write, description=f"write relation visible for {access_as!r}")

    await sharing_service.delete_shares([share_id], UserContext(user_id))

    # 1. Share row is gone.
    with pytest.raises(ValueError, match="Share not found"):
        await env.sharing_repo.get_share_by_id(share_id, UserContext(user_id))

    # 2. Temp access user is deleted.
    deleted_user = await env.user_repo.select(access_as)
    if deleted_user is not None:
        pytest.fail(
            f"temp access user {access_as!r} was not deleted; still present: {deleted_user!r}"
        )

    # 3. The access user no longer has any permission on the note.
    async def _cannot_view() -> bool:
        return not await env.permission_repo.has_permission(
            UserContext(access_as), "view", resource
        )

    await wait_until(
        _cannot_view,
        description=f"access user {access_as!r} no longer has 'view' on note {note_id!r}",
    )
    assert not await env.permission_repo.has_permission(
        UserContext(access_as), "write", resource
    )
    assert not await env.permission_repo.has_permission(
        UserContext(access_as), "delete", resource
    )
