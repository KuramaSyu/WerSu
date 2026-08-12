"""Storage contract for the role system.

Roles are split across two stores:

* the Postgres ``roles`` table holds role metadata (id, name,
  description, created_at);
* the SpiceDB ``user#member_of@role`` tuples hold role membership.

:class:`RoleRepoABC` abstracts both sides behind one contract.  The
service layer is responsible for permission enforcement; this repo
only translates requests into storage operations and surfaces the
persisted entities back to the caller.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.other.user_context import UserContextABC
from src.db.entities.user.role import (
    RoleEntity,
    RoleFilter,
    UserRoleMembershipEntity,
)


class RoleRepoABC(ABC):
    """Persistence contract for roles and role memberships.

    Implementations:
    * :class:`src.db.repos.permissions.spicedb_role_repo.SpicedbRoleRepo`
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

        Membership edges (``role:<id>#member@user:<id>``) and resource
        grants that target this role are **not** cleaned up here --
        SpiceDB has no "delete a role" operation, and the Postgres
        row deletion does not cascade into SpiceDB.  The caller's
        responsibility (or a follow-up sweeper) is to remove every
        member via :meth:`remove_user_from_role` and every resource
        grant out of band before calling ``delete_role``.

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
        """Fetch every role matching ``filter``.

        Args:
            filter: filter describing the roles to load.
            ctx: caller context, currently unused.

        Returns:
            List[RoleEntity]: the matching roles.  Empty when no
            row matches, including when the filter is empty
            (consistent with the other repos' empty-filter
            behaviour).
        """
        ...

    @abstractmethod
    async def add_user_to_role(
        self,
        user_id: str,
        role_id: str,
        ctx: UserContextABC,
    ) -> UserRoleMembershipEntity:
        """Insert the ``user#member_of@role`` tuple.

        Args:
            user_id: id of the user to add.
            role_id: id of the role to add the user to.
            ctx: caller context, currently unused.

        Returns:
            UserRoleMembershipEntity: the freshly created
            membership edge, with ``user_id``, ``role_id`` and a
            ``granted_at`` timestamp populated.
        """
        ...

    @abstractmethod
    async def remove_user_from_role(
        self,
        user_id: str,
        role_id: str,
        ctx: UserContextABC,
    ) -> None:
        """Delete the ``user#member_of@role`` tuple.

        No-op if the tuple does not exist.

        Args:
            user_id: id of the user to remove.
            role_id: id of the role to remove the user from.
            ctx: caller context, currently unused.
        """
        ...

    @abstractmethod
    async def get_roles_for_user(
        self,
        user_id: str,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        """List every role the given user is a member of.

        Args:
            user_id: id of the user to look up.
            ctx: caller context, currently unused.

        Returns:
            List[RoleEntity]: the roles the user belongs to, with
            metadata hydrated from Postgres.  Empty when the user is
            not a member of any role.
        """
        ...

    @abstractmethod
    async def get_users_for_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> List[UserRoleMembershipEntity]:
        """List every user that is a member of the given role.

        Args:
            role_id: id of the role to look up.
            ctx: caller context, currently unused.

        Returns:
            List[UserRoleMembershipEntity]: the membership edges,
            one per user.  Empty when no user is a member of the
            role.
        """
        ...


__all__ = ["RoleRepoABC"]