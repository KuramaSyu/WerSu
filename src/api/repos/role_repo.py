"""Storage contract for role metadata.

The Postgres ``roles`` table holds role metadata (id, name,
description, created_at).  Role membership lives in SpiceDB and is
managed by :class:`src.services.role_service.RoleServiceImpl` through
:class:`PermissionRepoABC`; the role repo therefore has no
membership methods and no permission-repo dependency.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.other.user_context import UserContextABC
from src.db.entities.user.role import RoleEntity, RoleFilter


class RoleRepoABC(ABC):
    """Persistence contract for role metadata.

    Implementations:
    * :class:`src.db.repos.permissions.postgres_role_repo.PostgresRoleRepo`
    """

    @abstractmethod
    async def create_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        """Insert a new role row.

        Args:
            role: role entity to insert.  ``id`` may be
                :obj:`~src.api.undefined.UNDEFINED` -- the repo
                assigns one.
            ctx: caller context, currently used for audit fields.

        Raises:
            ValueError: if ``role`` is missing required fields or has
                invalid values.

        Returns:
            RoleEntity: the persisted role, with ``id`` and
            ``created_at`` populated.
        """
        ...

    @abstractmethod
    async def update_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        """Update the role row identified by ``role.id``.

        :obj:`~src.api.undefined.UNDEFINED` fields are left
        untouched.  Use :obj:`None` to explicitly clear the
        description column.

        Args:
            role: role entity carrying ``id`` and the fields to
                update.  ``id`` must be set.
            ctx: caller context, currently used for audit fields.

        Raises:
            ValueError: if ``role.id`` is missing or the row no
                longer exists.

        Returns:
            RoleEntity: the updated role.
        """
        ...

    @abstractmethod
    async def delete_role(self, role_id: str, ctx: UserContextABC) -> None:
        """Delete the role row identified by ``role_id``.

        Membership edges and resource grants that target this role are
        **not** cleaned up here.  SpiceDB has no "delete a role"
        operation; callers must remove members through
        :class:`RoleServiceABC` before deleting the row.

        Args:
            role_id: id of the role to delete.
            ctx: caller context, currently unused.
        """
        ...

    @abstractmethod
    async def get_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> Optional[RoleEntity]:
        """Fetch a single role by id.

        Args:
            role_id: id of the role to load.
            ctx: caller context, currently unused.

        Returns:
            Optional[RoleEntity]: the matching role, or ``None`` if
            no row matches.
        """
        ...

    @abstractmethod
    async def get_roles(
        self,
        filter: RoleFilter,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        """Fetch every role matching ``filter.name``.

        Args:
            filter: filter describing the roles to load.  Callers must
                not pass ``member_id`` here; the service resolves that
                through SpiceDB first.
            ctx: caller context, currently unused.

        Returns:
            List[RoleEntity]: the matching roles.  Empty when no
            row matches.
        """
        ...


__all__ = ["RoleRepoABC"]
