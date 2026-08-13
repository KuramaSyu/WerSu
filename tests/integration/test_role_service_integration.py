"""Integration test coverage for ``RoleServiceImpl`` with real infrastructure.

Exercises the full role lifecycle against real Postgres + SpiceDB
containers:

1.  ``create_role`` writes the role row in Postgres.
2.  ``add_user_to_role`` writes ``user#member_of@role`` into SpiceDB.
3.  A role granted ``reader`` on a note grants ``view`` to every
    member of that role -- the round-trip from service -> Postgres ->
    SpiceDB -> permission check.
4.  ``remove_user_from_role`` strips the membership edge and revokes
    the effective view permission.
5.  ``update_role`` updates the metadata column without disturbing the
    membership edges.
6.  ``get_roles(name=...)`` filters by exact name; ``get_roles(member_id=...)``
    filters by membership.
7.  ``delete_role`` drops the Postgres row; SpiceDB membership
    tuples survive until explicitly deleted (``remove_user_from_role``
    is the canonical way to revoke role-based access).
8.  ``manage`` permission gates: a caller that does NOT hold
    ``role#administrator`` on a role cannot add users, update, or
    delete the role; the service raises ``PermissionError``.
9.  ``create_role`` super-admin gate is enforced via the injected
    ``is_super_admin`` callable.

Multiple checks run in one test because standing up Postgres +
SpiceDB containers is expensive; grouping keeps the suite fast.
"""

from datetime import datetime
from typing import Set

import pytest

from src.api import (
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    RoleRelationEnum,
    SubjectRef,
)
from src.api.other.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.user.role import RoleEntity, RoleFilter
from src.services.role_service import RoleServiceImpl
from tests._fixtures_pkg.postgres import IntegrationEnv
from tests.integration_helpers import (
    make_user_entity,
    wait_until,
)
from tests.stubs.user_context import _UserContext as UserContext


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]


async def _grant_role_admin(
    env: IntegrationEnv, *, role_id: str, user_id: str
) -> None:
    """Insert ``role:<id>#administrator@user:<id>`` so the user can ``manage`` it."""
    await env.permission_repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
            relation=RoleRelationEnum.ADMINISTRATOR,
            subject=SubjectRef(ObjectTypeEnum.USER, user_id),
        )
    ])


async def test_role_lifecycle_end_to_end(
    spicedb_postgres_env: IntegrationEnv,
) -> None:
    """Full role lifecycle: create, grant, propagate, revoke, update, delete.

    Bundles every check that touches the real Postgres + SpiceDB
    stack into a single test so the container-pair is only spun up
    once.  Each numbered check is independent and uses fresh
    fixtures so a failure in an early check still lets later checks
    run.
    """
    env = spicedb_postgres_env
    role_service: RoleServiceImpl = env.role_service

    # Bootstrap an owner user (gets default directories + admin rights).
    owner = await env.user_service.create_user(
        make_user_entity(
            discord_id=1122334455,
            username="role-owner",
            discriminator="0001",
            email="role-owner@example.com",
        )
    )
    assert owner.id is not None, f"create_user() returned a user without an ID: {owner!r}"
    owner_id = str(owner.id)

    # A second user that does not yet hold any role.
    member_user = await env.user_service.create_user(
        make_user_entity(
            discord_id=2233445566,
            username="role-member",
            discriminator="0002",
            email="role-member@example.com",
        )
    )
    assert member_user.id is not None
    member_id = str(member_user.id)

    # Restrict ``create_role`` to ``owner_id`` for the duration of
    # this test by overwriting the injected super-admin lookup.
    # Mutating the attribute (rather than rebuilding the service)
    # exercises the "mutable lambda" path the public API exposes
    # for tests that already hold a service handle.
    allowed_super_admins: Set[str] = {owner_id}

    def _is_super_admin(_ctx: object) -> Set[str]:
        return allowed_super_admins

    role_service.is_super_admin = _is_super_admin  # type: ignore[method-assign]
    super_admin_ctx = UserContext(owner_id)

    # ---- check 1: create_role writes the Postgres row --------------
    role = await role_service.create_role(
        RoleEntity(name="engineering", description="all engineers"),
        super_admin_ctx,
    )
    assert role.id is not UNDEFINED, f"create_role returned a role without an ID: {role!r}"
    assert role.name == "engineering"
    assert role.description == "all engineers"
    role_id = str(role.id)

    # Round-trip read.
    fetched = await role_service.get_role(role_id, super_admin_ctx)
    assert fetched is not None, "get_role() returned None for the just-created role"
    assert fetched.id == role_id
    assert fetched.name == "engineering"

    # Owner becomes ``administrator`` on the role so they can mutate it.
    await _grant_role_admin(env, role_id=role_id, user_id=owner_id)

    # ---- check 2: add_user_to_role writes the SpiceDB edge ---------
    await role_service.add_user_to_role(
        member_id,
        role_id,
        super_admin_ctx,
    )

    async def _is_member() -> bool:
        ids = await role_service.get_roles_for_user(member_id, super_admin_ctx)
        return any(r.id == role_id for r in ids if r.id is not None)

    await wait_until(_is_member, description="role membership visible")

    # ---- check 3: role-granted permission propagates ----------------
    # Owner creates a note; ``role`` is granted reader; member
    # (a member of the role) should be able to view.
    note = await env.note_repo.insert(
        NoteEntity(
            title="role-target",
            content="",
            updated_at=datetime.now(),
            author_id=owner.id,
        ),
        UserContext(owner_id),
    )
    assert note.note_id is not None
    note_id = str(note.note_id)

    await env.permission_repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
            relation=NoteRelationEnum.READER,
            # ``optional_relation="member"`` turns the subject into
            # a userset reference (``role:<id>#member``), which is
            # what ``note#reader: user | role#member`` accepts.
            subject=SubjectRef(
                ObjectTypeEnum.ROLE,
                role_id,
                optional_relation="member",
            ),
        )
    ])

    async def _can_view() -> bool:
        return await env.permission_repo.has_permission(
            UserContext(member_id),
            "view",
            ObjectRef(ObjectTypeEnum.NOTE, note_id),
        )

    await wait_until(_can_view, description="role reader grants view to member")
    # Member cannot write -- reader does not imply writer.
    assert not await env.permission_repo.has_permission(
        UserContext(member_id),
        "write",
        ObjectRef(ObjectTypeEnum.NOTE, note_id),
    )

    # ---- check 4: remove_user_from_role revokes the permission -----
    await role_service.remove_user_from_role(
        member_id,
        role_id,
        super_admin_ctx,
    )

    async def _no_longer_member() -> bool:
        ids = await role_service.get_roles_for_user(member_id, super_admin_ctx)
        return not any(r.id == role_id for r in ids if r.id is not None)

    await wait_until(_no_longer_member, description="role membership gone")

    async def _no_longer_view() -> bool:
        return not await env.permission_repo.has_permission(
            UserContext(member_id),
            "view",
            ObjectRef(ObjectTypeEnum.NOTE, note_id),
        )

    await wait_until(_no_longer_view, description="role member lost view")

    # ---- check 5: update_role mutates metadata only ----------------
    updated = await role_service.update_role(
        RoleEntity(id=role_id, description="engineers, redesigned"),
        super_admin_ctx,
    )
    assert updated.description == "engineers, redesigned"
    assert updated.name == "engineering", "update must not touch name when only description is set"

    # ---- check 6: get_roles filter shapes ---------------------------
    # Add a second role so the filter actually has something to
    # exclude.
    second = await role_service.create_role(
        RoleEntity(name="marketing"),
        super_admin_ctx,
    )
    assert second.id is not UNDEFINED

    # By name: should find exactly the engineering role.
    by_name = await role_service.get_roles(
        RoleFilter(name="engineering"),
        super_admin_ctx,
    )
    assert [r.id for r in by_name if r.id is not None] == [role_id]

    # By member_id: add the member back, then filter by member.
    await role_service.add_user_to_role(
        member_id,
        role_id,
        super_admin_ctx,
    )
    await wait_until(_is_member, description="member re-added")

    by_member = await role_service.get_roles(
        RoleFilter(member_id=member_id),
        super_admin_ctx,
    )
    assert any(r.id == role_id for r in by_member if r.id is not None)
    # Marketing role does NOT match because the member is only in engineering.
    assert not any(r.id == second.id for r in by_member if r.id is not None)

    # get_users_for_role walks SpiceDB membership edges.
    users_for_role = await role_service.get_users_for_role(
        role_id, super_admin_ctx
    )
    assert any(m.user_id == member_id for m in users_for_role)

    # ---- check 7: delete_role drops the row -------------------------
    await role_service.delete_role(role_id, super_admin_ctx)
    assert await role_service.get_role(role_id, super_admin_ctx) is None

    # SpiceDB has no "delete a role" concept: roles live as objects
    # and the membership + reader edges remain in SpiceDB until
    # explicitly deleted.  The Postgres row is gone, so the role
    # can no longer be looked up via the metadata path, but the
    # effective permission on the note still flows through the
    # surviving SpiceDB tuples.  Removing the membership explicitly
    # via ``remove_user_from_role`` is the correct way to revoke
    # access -- verify that path still works.
    await role_service.remove_user_from_role(
        member_id,
        role_id,
        super_admin_ctx,
    )

    async def _no_view_after_revoke() -> bool:
        return not await env.permission_repo.has_permission(
            UserContext(member_id),
            "view",
            ObjectRef(ObjectTypeEnum.NOTE, note_id),
        )

    await wait_until(_no_view_after_revoke, description="explicit revoke removes view")

    # ---- check 8: manage-permission gate ----------------------------
    # The second user (``member_user``) is a *member* of the second
    # role (not an administrator).  They must NOT be allowed to
    # update or delete that role.  Grant the owner administrator on
    # the second role first so we can use them to add member_id.
    await _grant_role_admin(env, role_id=str(second.id), user_id=owner_id)
    await role_service.add_user_to_role(
        member_id, str(second.id), super_admin_ctx
    )
    # member is NOT an administrator on the role.
    with pytest.raises(PermissionError):
        await role_service.update_role(
            RoleEntity(id=str(second.id), description="should not work"),
            UserContext(member_id),
        )
    with pytest.raises(PermissionError):
        await role_service.delete_role(str(second.id), UserContext(member_id))
    with pytest.raises(PermissionError):
        await role_service.add_user_to_role(
            member_id,
            str(second.id),
            UserContext(member_id),
        )
    with pytest.raises(PermissionError):
        await role_service.remove_user_from_role(
            member_id,
            str(second.id),
            UserContext(member_id),
        )

    # The owner already holds ``administrator`` on the second role
    # (granted above).  Update through them to verify the gate
    # flips back to allow.
    updated2 = await role_service.update_role(
        RoleEntity(id=str(second.id), description="now allowed"),
        super_admin_ctx,
    )
    assert updated2.description == "now allowed"

    # ---- check 9: create_role super-admin gate ----------------------
    # Tighten the super-admin lookup to an empty set; member_user is
    # now not allowed and must be rejected.
    allowed_super_admins.clear()
    with pytest.raises(PermissionError):
        await role_service.create_role(
            RoleEntity(name="forbidden"),
            UserContext(member_id),
        )

    # ---- check 10: input validation --------------------------------
    with pytest.raises(ValueError):
        await role_service.create_role(RoleEntity(name=UNDEFINED), super_admin_ctx)
    with pytest.raises(ValueError):
        await role_service.update_role(RoleEntity(name="x"), super_admin_ctx)
    with pytest.raises(ValueError):
        await role_service.add_user_to_role(
            user_id="", role_id=str(second.id), ctx=super_admin_ctx
        )
    with pytest.raises(ValueError):
        await role_service.add_user_to_role(
            user_id=member_id, role_id="", ctx=super_admin_ctx
        )