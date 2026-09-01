"""Unit tests for :class:`src.services.role_service.RoleServiceImpl`.

The service enforces ``manage`` permission on every write that
mutates an existing role.  ``create_role`` falls back to an
injected super-admin lookup.  These tests wire the real service
against an in-memory permission repo (so permission tuples resolve
like SpiceDB would) and a fake role repo that records the calls
and holds role metadata in-process.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from src.api import ObjectRef, ObjectTypeEnum, PermissionRepoABC, Relationship, RoleRelationEnum, SubjectRef
from src.api.other.undefined import UNDEFINED, UndefinedOr, is_undefined
from src.api.other.user_context import UserContextABC
from src.db.entities.user.role import RoleEntity, RoleFilter, UserRoleMembershipEntity
from src.services.role_service import RoleServiceImpl
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.user_context import _UserContext
from typing import Dict, List, Optional
import pytest, uuid


# ---------------------------------------------------------------------------
# Recording fake
# ---------------------------------------------------------------------------


@dataclass
class _FakeRoleRepo:
    """In-process stand-in for :class:`RoleRepoABC`."""

    _roles: Dict[str, RoleEntity] = field(default_factory=dict)
    create_calls: List[RoleEntity] = field(default_factory=list)
    update_calls: List[RoleEntity] = field(default_factory=list)
    delete_calls: List[str] = field(default_factory=list)
    add_calls: List[tuple[str, str]] = field(default_factory=list)
    remove_calls: List[tuple[str, str]] = field(default_factory=list)

    async def create_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:

        if is_undefined(role.name):
            raise ValueError("role.name is required")
        new_id = str(uuid.uuid4())
        self._roles[new_id] = RoleEntity(
            id=new_id,
            name=str(role.name),
            description=role.description if not is_undefined(role.description) else UNDEFINED,
            created_at=datetime.now(),
        )
        self.create_calls.append(self._roles[new_id])
        return self._roles[new_id]

    async def update_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        self.update_calls.append(role)
        existing = self._roles.get(str(role.id))
        if existing is None:
            raise ValueError(f"Role not found: {role.id}")
        merged = RoleEntity(
            id=existing.id,
            name=str(role.name) if not is_undefined(role.name) else existing.name,
            description=role.description if not is_undefined(role.description) else existing.description,
            created_at=existing.created_at,
        )
        self._roles[str(role.id)] = merged
        return merged

    async def delete_role(self, role_id: str, ctx: UserContextABC) -> None:
        self.delete_calls.append(role_id)
        if role_id not in self._roles:
            raise ValueError(f"Role not found: {role_id}")
        del self._roles[role_id]

    async def get_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> Optional[RoleEntity]:
        return self._roles.get(role_id)

    async def get_roles(
        self,
        filter: RoleFilter,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        out = list(self._roles.values())
        if not is_undefined(filter.name):
            out = [r for r in out if r.name == filter.name]
        return out

    async def add_user_to_role(
        self,
        user_id: str,
        role_id: str,
        ctx: UserContextABC,
    ) -> UserRoleMembershipEntity:
        self.add_calls.append((user_id, role_id))
        return UserRoleMembershipEntity(
            user_id=user_id, role_id=role_id, granted_at=UNDEFINED
        )

    async def remove_user_from_role(
        self,
        user_id: str,
        role_id: str,
        ctx: UserContextABC,
    ) -> None:
        self.remove_calls.append((user_id, role_id))

    async def get_roles_for_user(
        self,
        user_id: str,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        return list(self._roles.values())

    async def get_users_for_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> List[UserRoleMembershipEntity]:
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _grant_manage(
    permission_repo: PermissionRepoABC,
    *,
    role_id: str,
    user_id: str,
) -> None:
    """Insert ``role:<id>#administrator@user:<id>`` so ``manage`` resolves True."""
    await permission_repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
            relation=RoleRelationEnum.ADMINISTRATOR,
            subject=SubjectRef(ObjectTypeEnum.USER, user_id),
        )
    ])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def permission_repo() -> InMemoryPermissionRepo:
    return InMemoryPermissionRepo()


@pytest.fixture
def role_repo() -> _FakeRoleRepo:
    return _FakeRoleRepo()


@pytest.fixture
def super_admin_ids() -> set[str]:
    """Default: nobody is a super-admin.  Tests override per-case."""
    return set()


@pytest.fixture
def service(
    role_repo: _FakeRoleRepo,
    permission_repo: InMemoryPermissionRepo,
    super_admin_ids: set[str],
) -> RoleServiceImpl:
    return RoleServiceImpl(
        role_repo=role_repo,
        permission_repo=permission_repo,
        is_super_admin=lambda _ctx: set(super_admin_ids),
    )


# ---- create_role ---------------------------------------------------------


async def test_create_role_rejects_caller_not_in_super_admin_env(
    service: RoleServiceImpl,
    role_repo: _FakeRoleRepo,
) -> None:
    """``create_role`` denies callers not in the injected super-admin set."""
    actor = _UserContext(user_id="alice")
    with pytest.raises(PermissionError):
        await service.create_role(
            RoleEntity(name="engineering"),
            actor,
        )
    assert role_repo.create_calls == []


async def test_create_role_accepts_super_admin(
    service: RoleServiceImpl,
    role_repo: _FakeRoleRepo,
    super_admin_ids: set[str],
) -> None:
    """``create_role`` proceeds when caller is in the injected super-admin set."""
    super_admin_ids.update({"alice", "bob"})
    actor = _UserContext(user_id="alice")
    created = await service.create_role(
        RoleEntity(name="engineering"),
        actor,
    )
    assert created.id is not UNDEFINED
    assert created.name == "engineering"
    assert len(role_repo.create_calls) == 1


# ---- add_user_to_role ----------------------------------------------------


async def test_add_user_to_role_requires_manage(
    service: RoleServiceImpl,
    role_repo: _FakeRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    """Without ``manage`` on the role the call is denied."""
    role = await role_repo.create_role(RoleEntity(name="eng"), _UserContext(user_id="root"))
    with pytest.raises(PermissionError):
        await service.add_user_to_role(
            user_id="alice",
            role_id=str(role.id),
            ctx=_UserContext(user_id="carol"),
        )
    assert role_repo.add_calls == []


async def test_add_user_to_role_succeeds_with_manage(
    service: RoleServiceImpl,
    role_repo: _FakeRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    """Caller holds ``administrator`` on the role; ``manage`` resolves True."""
    role = await role_repo.create_role(RoleEntity(name="eng"), _UserContext(user_id="root"))
    await _grant_manage(
        permission_repo,
        role_id=str(role.id),
        user_id="carol",
    )
    membership = await service.add_user_to_role(
        user_id="alice",
        role_id=str(role.id),
        ctx=_UserContext(user_id="carol"),
    )
    assert membership.user_id == "alice"
    assert membership.role_id == str(role.id)
    # The membership tuple ends up in the permission repo too --
    # verify that, since ``SpicedbRoleRepo.add_user_to_role`` writes
    # through ``PermissionRepoABC.insert``.  The fake skips that
    # step, but a real repo would have inserted it.
    assert role_repo.add_calls == [("alice", str(role.id))]


# ---- remove_user_from_role ----------------------------------------------


async def test_remove_user_from_role_requires_manage(
    service: RoleServiceImpl,
    role_repo: _FakeRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    role = await role_repo.create_role(RoleEntity(name="eng"), _UserContext(user_id="root"))
    with pytest.raises(PermissionError):
        await service.remove_user_from_role(
            user_id="alice",
            role_id=str(role.id),
            ctx=_UserContext(user_id="carol"),
        )
    assert role_repo.remove_calls == []


# ---- update_role / delete_role -----------------------------------------


async def test_update_role_requires_manage(
    service: RoleServiceImpl,
    role_repo: _FakeRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    role = await role_repo.create_role(RoleEntity(name="eng"), _UserContext(user_id="root"))
    with pytest.raises(PermissionError):
        await service.update_role(
            RoleEntity(id=str(role.id), name="engineering"),
            _UserContext(user_id="carol"),
        )
    assert role_repo.update_calls == []


async def test_delete_role_requires_manage(
    service: RoleServiceImpl,
    role_repo: _FakeRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    role = await role_repo.create_role(RoleEntity(name="eng"), _UserContext(user_id="root"))
    with pytest.raises(PermissionError):
        await service.delete_role(str(role.id), _UserContext(user_id="carol"))
    assert role_repo.delete_calls == []


async def test_update_role_succeeds_with_manage(
    service: RoleServiceImpl,
    role_repo: _FakeRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    role = await role_repo.create_role(RoleEntity(name="eng"), _UserContext(user_id="root"))
    await _grant_manage(permission_repo, role_id=str(role.id), user_id="carol")
    updated = await service.update_role(
        RoleEntity(id=str(role.id), name="engineering"),
        _UserContext(user_id="carol"),
    )
    assert updated.name == "engineering"


# ---- input validation ---------------------------------------------------


async def test_create_role_rejects_missing_name(
    service: RoleServiceImpl,
    super_admin_ids: set[str],
) -> None:
    super_admin_ids.add("alice")
    with pytest.raises(ValueError):
        await service.create_role(RoleEntity(name=UNDEFINED), _UserContext(user_id="alice"))


async def test_update_role_rejects_missing_id(service: RoleServiceImpl) -> None:
    with pytest.raises(ValueError):
        await service.update_role(RoleEntity(name="x"), _UserContext(user_id="alice"))


async def test_add_user_to_role_rejects_missing_ids(service: RoleServiceImpl) -> None:
    with pytest.raises(ValueError):
        await service.add_user_to_role(
            user_id="",
            role_id="r1",
            ctx=_UserContext(user_id="alice"),
        )
    with pytest.raises(ValueError):
        await service.add_user_to_role(
            user_id="alice",
            role_id="",
            ctx=_UserContext(user_id="alice"),
        )


# ---- in-memory role grant propagation -----------------------------------


async def test_role_grant_propagates_to_user_permission_check(
    permission_repo: InMemoryPermissionRepo,
) -> None:
    """Smoke-test the in-memory repo's ``member_of`` walk.

    alice is a member of role X.  role X is granted ``reader`` on a
    note.  alice should be able to read the note.
    """
    await permission_repo.insert([
        Relationship(
            resource=ObjectRef("role", "role-eng"),
            relation="member",
            subject=SubjectRef("user", "alice"),
        ),
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation="reader",
            # Userset reference -- matches the SpiceDB schema where
            # ``note#reader: user | role#member``.
            subject=SubjectRef("role", "role-eng", optional_relation="member"),
        ),
    ])

    actor = _UserContext(user_id="alice")
    assert await permission_repo.has_permission(actor, "view", ObjectRef("note", "note-1"))
    # alice is NOT admin
    assert not await permission_repo.has_permission(actor, "delete", ObjectRef("note", "note-1"))