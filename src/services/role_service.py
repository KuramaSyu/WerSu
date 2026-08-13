"""Concrete implementation of :class:`RoleServiceABC`.

Wires the Postgres + SpiceDB role repo to permission checks.  Every
write path that mutates an *existing* role is gated on
``manage`` permission on that role.  ``create_role`` has no role to
gate on yet, so it falls back to a super-admin concept: a caller-
supplied callable that returns the set of user ids allowed to
bootstrap new roles.

The super-admin lookup is injected rather than read from
``os.environ`` directly so tests can either:

* construct their own :class:`RoleServiceImpl` with a lambda that
  returns a fixed set of ids, or
* mutate the lambda after construction (e.g. ``service.is_super_admin
  = lambda ctx: True``) and have the change take effect on the next
  ``create_role`` call without restarting the service.
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional

from src.api import (
    PermissionRepoABC,
    RoleRepoABC,
    RoleServiceABC,
)
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.other.user_context import UserContextABC
from src.api.services.role import RoleServiceABC
from src.db.entities.user.role import (
    RoleEntity,
    RoleFilter,
    UserRoleMembershipEntity,
)
from src.utils import logging_provider as default_logging_provider


# Callable returning the set of user ids allowed to bootstrap new
# roles.  Receives the caller :class:`UserContextABC` so test
# implementations can vary the answer per request (e.g. only allow
# admins), but production wiring is context-agnostic.
SuperAdminLookup = Callable[[UserContextABC], set[str]]


def default_super_admin_lookup(_: UserContextABC) -> set[str]:
    """Read ``ROLE_SUPER_ADMINS`` (comma-separated user ids) on every call.

    Re-reads on each invocation so test fixtures that mutate
    ``os.environ`` take effect without restarting the service.
    Empty set means no caller can create roles -- this is the safe
    default for fresh deployments.
    """
    raw = os.environ.get("ROLE_SUPER_ADMINS", "")
    return {uid.strip() for uid in raw.split(",") if uid.strip()}


class RoleServiceImpl(RoleServiceABC):
    """Role service with ``manage`` permission gating."""

    def __init__(
        self,
        role_repo: RoleRepoABC,
        permission_repo: PermissionRepoABC,
        logging_provider: Optional[LoggingProvider] = None,
        *,
        is_super_admin: Optional[SuperAdminLookup] = None,
    ) -> None:
        self._role_repo = role_repo
        self._permission_repo = permission_repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)
        # Mutable so tests can swap or mutate after construction
        # without rebuilding the service.  Production wiring passes
        # ``None`` and falls back to the env-var lookup.
        self.is_super_admin: SuperAdminLookup = (
            is_super_admin if is_super_admin is not None else default_super_admin_lookup
        )

    # ---- write paths -----------------------------------------------------

    async def create_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        if is_undefined(role.name) or role.name is None:
            raise ValueError("role.name is required")
        if ctx.user_id not in self.is_super_admin(ctx):
            raise PermissionError(
                "user is not allowed to create roles (super-admin required)"
            )
        return await self._role_repo.create_role(role, ctx)

    async def update_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        if is_undefined(role.id) or role.id is None:
            raise ValueError("role.id is required")
        await self._assert_can_manage(str(role.id), ctx)
        return await self._role_repo.update_role(role, ctx)

    async def delete_role(self, role_id: str, ctx: UserContextABC) -> None:
        if not role_id:
            raise ValueError("role_id is required")
        await self._assert_can_manage(role_id, ctx)
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
        await self._assert_can_manage(role_id, ctx)
        return await self._role_repo.add_user_to_role(user_id, role_id, ctx)

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
        await self._assert_can_manage(role_id, ctx)
        await self._role_repo.remove_user_from_role(user_id, role_id, ctx)

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
        return await self._role_repo.get_roles(filter, ctx)

    async def get_roles_for_user(
        self,
        user_id: str,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        if not user_id:
            raise ValueError("user_id is required")
        return await self._role_repo.get_roles_for_user(user_id, ctx)

    async def get_users_for_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> List[UserRoleMembershipEntity]:
        if not role_id:
            raise ValueError("role_id is required")
        return await self._role_repo.get_users_for_role(role_id, ctx)

    # ---- internals -------------------------------------------------------

    async def _assert_can_manage(self, role_id: str, ctx: UserContextABC) -> None:
        """Ensure the caller holds ``manage`` on the role.

        Raises:
            PermissionError: caller lacks ``manage`` on the role.
        """
        from src.domain.permission_chain import HasRoleManagePerm

        chain = HasRoleManagePerm(role_id).set_permission_repo(self._permission_repo)
        result = await chain.check(ctx)
        if not result:
            raise PermissionError(chain.error)


__all__ = ["RoleServiceImpl"]