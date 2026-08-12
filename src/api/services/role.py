"""Application service for role CRUD and membership management.

The service enforces the ``manage`` permission on every role write
path:

* ``add_user_to_role`` and ``remove_user_from_role`` -- the caller
  must hold ``manage`` on the *role* being modified, not on the
  resource that grants the role.  Note admins can attach a role to
  a note without being able to edit the role's membership.
* ``update_role`` and ``delete_role`` -- gated on ``manage`` on the
  role itself.

``create_role`` is the only write that has no resource to gate on,
because there is no role yet.  Implementations are expected to gate
``create_role`` on a bootstrap concept (e.g. an env-var list of
super-admin user ids, or a sentinel ``role:[global]super-admin``
whose ``manage`` permission the caller holds).

Implementations:
* :class:`src.services.role_service.RoleServiceImpl`
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.other.user_context import UserContextABC
from src.db.entities.user.role import (
    RoleEntity,
    RoleFilter,
    UserRoleMembershipEntity,
)


class RoleServiceABC(ABC):
    """Abstract application service for the role entity."""

    @abstractmethod
    async def create_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        """Create a new role.

        Args:
            role: role to create.  ``name`` is required; ``id`` and
                ``created_at`` are assigned by the repo.
            ctx: caller context.  Implementations gate ``create_role``
                on a bootstrap concept -- callers cannot rely on a
                ``manage`` permission on a non-existent role.

        Returns:
            RoleEntity: the persisted role.

        Raises:
            ValueError: ``role.name`` is missing.
            PermissionError: caller is not allowed to create roles.
        """
        ...

    @abstractmethod
    async def update_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        """Update a role's metadata.

        Args:
            role: role carrying ``id`` and the fields to update.
            ctx: caller context.  Must hold ``manage`` on the role.

        Returns:
            RoleEntity: the updated role.

        Raises:
            ValueError: ``role.id`` is missing.
            LookupError: the role no longer exists.
            PermissionError: caller lacks ``manage`` on the role.
        """
        ...

    @abstractmethod
    async def delete_role(self, role_id: str, ctx: UserContextABC) -> None:
        """Delete a role.

        Membership edges and resource grants targeting the role are
        not cleaned up; SpiceDB has no "delete a role" operation,
        so callers must remove every member via
        :meth:`add_user_to_role` / :meth:`remove_user_from_role` and
        every resource grant out of band before calling this method.

        Args:
            role_id: id of the role to delete.
            ctx: caller context.  Must hold ``manage`` on the role.

        Raises:
            ValueError: ``role_id`` is missing.
            LookupError: the role no longer exists.
            PermissionError: caller lacks ``manage`` on the role.
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
            ctx: caller context.

        Returns:
            Optional[RoleEntity]: the matching role, or ``None``
            when no row exists.
        """
        ...

    @abstractmethod
    async def get_roles(
        self,
        filter: RoleFilter,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        """Fetch every role matching ``filter``.

        Args:
            filter: filter describing the roles to load.
            ctx: caller context.

        Returns:
            List[RoleEntity]: the matching roles.
        """
        ...

    @abstractmethod
    async def add_user_to_role(
        self,
        user_id: str,
        role_id: str,
        ctx: UserContextABC,
    ) -> UserRoleMembershipEntity:
        """Add ``user_id`` as a member of ``role_id``.

        Args:
            user_id: id of the user to add.
            role_id: id of the role to add the user to.
            ctx: caller context.  Must hold ``manage`` on the role.

        Returns:
            UserRoleMembershipEntity: the freshly created
            membership edge.

        Raises:
            ValueError: ``user_id`` or ``role_id`` is missing.
            LookupError: the role no longer exists.
            PermissionError: caller lacks ``manage`` on the role.
        """
        ...

    @abstractmethod
    async def remove_user_from_role(
        self,
        user_id: str,
        role_id: str,
        ctx: UserContextABC,
    ) -> None:
        """Remove ``user_id`` from ``role_id``'s membership.

        Args:
            user_id: id of the user to remove.
            role_id: id of the role to remove the user from.
            ctx: caller context.  Must hold ``manage`` on the role.

        Raises:
            ValueError: ``user_id`` or ``role_id`` is missing.
            PermissionError: caller lacks ``manage`` on the role.
        """
        ...

    @abstractmethod
    async def get_roles_for_user(
        self,
        user_id: str,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        """List every role the user is a member of.

        Args:
            user_id: id of the user to look up.
            ctx: caller context.

        Returns:
            List[RoleEntity]: the user's roles.  Empty when the
            user is not a member of any role.
        """
        ...

    @abstractmethod
    async def get_users_for_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> List[UserRoleMembershipEntity]:
        """List every user that is a member of the role.

        Args:
            role_id: id of the role to look up.
            ctx: caller context.

        Returns:
            List[UserRoleMembershipEntity]: the membership edges.
        """
        ...


__all__ = ["RoleServiceABC"]