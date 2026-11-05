"""Concrete implementation of :class:`RoleServiceABC`.

Wires the Postgres role-metadata repo to SpiceDB permission checks.
Membership writes are gated on ``can_grant_member`` and the
corresponding SpiceDB tuples live here in the service, not in the
Postgres role repo.  Role metadata writes are gated on the role
``admin`` relation.

``create_role`` has no role to gate on yet, so it checks the global
``page#admin`` relation instead.  Page admins are seeded by the
``20261120-add-page-administrator`` migration (through the
``Page Administrator`` role); no environment variable is required.

After inserting the role row, the service grants the creator
``role#admin`` and ``role#member`` so they hold every permission of the
new role, including ``can_grant_member``.

Tests can inject ``is_page_admin`` (or the legacy ``is_super_admin``
set-returning hook) to bypass SpiceDB without rebuilding the service.
"""

from __future__ import annotations

import inspect
from typing import Awaitable, Callable, List, Optional, Union

from src.api import (
    ObjectRef,
    ObjectTypeEnum,
    PermissionRepoABC,
    Relationship,
    RoleRelationEnum,
    SubjectRef,
    UserContextABC,
)
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.repos.role_repo import RoleRepoABC
from src.api.services.role import RoleServiceABC
from src.db.entities.user.role import (
    RoleEntity,
    RoleFilter,
    UserRoleMembershipEntity,
)
from src.utils import logging_provider as default_logging_provider


# Callables used by tests to bypass the SpiceDB page-admin check.
PageAdminLookup = Callable[[UserContextABC], Union[bool, Awaitable[bool]]]
SuperAdminLookup = Callable[[UserContextABC], set[str]]


class RoleServiceImpl(RoleServiceABC):
    """Role service with capability-based gating and membership storage."""

    def __init__(
        self,
        role_repo: RoleRepoABC,
        permission_repo: PermissionRepoABC,
        logging_provider: Optional[LoggingProvider] = None,
        *,
        is_page_admin: Optional[PageAdminLookup] = None,
        is_super_admin: Optional[SuperAdminLookup] = None,
    ) -> None:
        self._role_repo = role_repo
        self._permission_repo = permission_repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)
        # Mutable so tests can swap after construction without rebuilding
        # the service.  ``None`` means "use the SpiceDB page-admin check".
        self.is_page_admin: Optional[PageAdminLookup] = is_page_admin
        # Backwards-compatible test hook: pre-page-admin tests inject a
        # set-returning super-admin lookup.  Production never sets this.
        self.is_super_admin: Optional[SuperAdminLookup] = is_super_admin

    # ---- write paths -----------------------------------------------------

    async def create_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        if is_undefined(role.name) or role.name is None:
            raise ValueError("role.name is required")
        if not await self._is_page_admin(ctx):
            raise PermissionError(
                "user is not allowed to create roles (page admin required)"
            )

        creator_id = self._creator_id(ctx)
        created = await self._role_repo.create_role(role, ctx)
        if is_undefined(created.id) or created.id is None:
            raise ValueError("Failed to create role: no id returned")

        # A freshly created role would otherwise have neither an admin
        # nor a member.  Grant the creator both.
        role_id = str(created.id)
        await self._permission_repo.insert([
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
                relation=RoleRelationEnum.ADMIN,
                subject=SubjectRef(ObjectTypeEnum.USER, creator_id),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
                relation=RoleRelationEnum.MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, creator_id),
            ),
        ])
        return created

    async def update_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        if is_undefined(role.id) or role.id is None:
            raise ValueError("role.id is required")
        await self._assert_admin(str(role.id), ctx)
        return await self._role_repo.update_role(role, ctx)

    async def delete_role(self, role_id: str, ctx: UserContextABC) -> None:
        if not role_id:
            raise ValueError("role_id is required")
        await self._assert_admin(role_id, ctx)
        await self._role_repo.delete_role(role_id, ctx)

    async def add_user_to_role(
        self,
        user_id: str,
        role_id: str,
        ctx: UserContextABC,
    ) -> UserRoleMembershipEntity:
        if not user_id:
            raise ValueError("user_id is required")
        if not role_id:
            raise ValueError("role_id is required")
        await self._assert_can_grant_member(role_id, ctx)
        await self._permission_repo.insert([
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
                relation=RoleRelationEnum.MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        ])
        return UserRoleMembershipEntity(
            user_id=user_id,
            role_id=role_id,
            granted_at=UNDEFINED,
        )

    async def remove_user_from_role(
        self,
        user_id: str,
        role_id: str,
        ctx: UserContextABC,
    ) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        if not role_id:
            raise ValueError("role_id is required")
        await self._assert_can_grant_member(role_id, ctx)
        await self._permission_repo.delete(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
                relation=RoleRelationEnum.MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        )

    # ---- read paths ------------------------------------------------------

    async def get_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> Optional[RoleEntity]:
        if not role_id:
            raise ValueError("role_id is required")
        return await self._role_repo.get_role(role_id, ctx)

    async def get_roles(
        self,
        filter: RoleFilter,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        if is_undefined(filter.member_id):
            return await self._role_repo.get_roles(filter, ctx)

        role_ids = await self._role_ids_for_user(str(filter.member_id), ctx)
        if not role_ids:
            return []

        # ``member_id`` is resolved in SpiceDB; the Postgres repo only
        # understands the name filter.
        roles = await self._role_repo.get_roles(RoleFilter(name=filter.name), ctx)
        allowed = set(role_ids)
        return [role for role in roles if str(role.id) in allowed]

    async def get_roles_for_user(
        self,
        user_id: str,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        if not user_id:
            raise ValueError("user_id is required")
        roles: list[RoleEntity] = []
        for role_id in await self._role_ids_for_user(user_id, ctx):
            role = await self._role_repo.get_role(role_id, ctx)
            if role is not None:
                roles.append(role)
        return roles

    async def get_users_for_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> List[UserRoleMembershipEntity]:
        if not role_id:
            raise ValueError("role_id is required")
        tuples = await self._permission_repo.lookup_relationships(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
                relation=RoleRelationEnum.MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, UNDEFINED),
            )
        )
        return [
            UserRoleMembershipEntity(
                user_id=(
                    str(t.subject.object_id)
                    if not is_undefined(t.subject.object_id)
                    else UNDEFINED
                ),
                role_id=role_id,
                granted_at=UNDEFINED,
            )
            for t in tuples
            if str(t.subject.object_type) == ObjectTypeEnum.USER.value
        ]

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _creator_id(ctx: UserContextABC) -> str:
        creator_id = getattr(ctx, "user_id", None)
        if not creator_id:
            raise ValueError(
                "ctx.user_id is required to create a role "
                "(the creator is granted its permissions)"
            )
        return str(creator_id)

    async def _role_ids_for_user(
        self,
        user_id: str,
        ctx: UserContextABC,
    ) -> List[str]:
        """Return role ids the user is a member of, from SpiceDB."""
        tuples = await self._permission_repo.lookup_relationships(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, UNDEFINED),
                relation=RoleRelationEnum.MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        )
        ids: list[str] = []
        for t in tuples:
            if (
                str(t.resource.object_type) == ObjectTypeEnum.ROLE.value
                and not is_undefined(t.resource.object_id)
            ):
                ids.append(str(t.resource.object_id))
        return ids

    async def _is_page_admin(self, ctx: UserContextABC) -> bool:
        """Return whether ``ctx`` may perform page-wide role writes.

        Resolution order:

        1. ``is_super_admin`` -- legacy test hook returning allowed ids.
        2. ``is_page_admin`` -- test hook returning a bool (or awaitable).
        3. SpiceDB ``page:global#admin`` -- the production path.
        """
        if self.is_super_admin is not None:
            return ctx.user_id in self.is_super_admin(ctx)
        if self.is_page_admin is not None:
            result = self.is_page_admin(ctx)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)

        from src.domain.permission_chain import HasPageAdminPerm

        chain = HasPageAdminPerm().set_permission_repo(self._permission_repo)
        return bool(await chain.check(ctx))

    async def _assert_admin(self, role_id: str, ctx: UserContextABC) -> None:
        """Ensure the caller holds the role's ``admin`` relation."""
        from src.domain.permission_chain import HasRoleAdminPerm

        chain = HasRoleAdminPerm(role_id).set_permission_repo(
            self._permission_repo
        )
        result = await chain.check(ctx)
        if not result:
            raise PermissionError(chain.error)

    async def _assert_can_grant_member(self, role_id: str, ctx: UserContextABC) -> None:
        """Ensure the caller may write/remove ``role#member``."""
        from src.domain.permission_chain import HasRoleGrantMemberPerm

        chain = HasRoleGrantMemberPerm(role_id).set_permission_repo(
            self._permission_repo
        )
        result = await chain.check(ctx)
        if not result:
            raise PermissionError(chain.error)


__all__ = ["RoleServiceImpl"]