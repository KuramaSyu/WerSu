"""Domain entities for the role / RBAC system.

Roles are SpiceDB objects (``definition role {}``) that group users
together so permissions can be granted to the role as a whole instead
of to each user individually.  The metadata (name, description) lives
in Postgres; the membership edges (``user#member_of@role``) live in
SpiceDB.  A :class:`UserRoleMembershipEntity` joins the two halves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List

from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr, is_undefined
from src.api.other.visitor import AcceptsVisitor, EntityVisitor


@dataclass
class RoleEntity(AcceptsVisitor):
    """A role that bundles a set of users for permission grants.

    Attributes:
        id: SpiceDB / Postgres id (UUIDv7).  Use
            :obj:`~src.api.undefined.UNDEFINED` to let the repo
            assign one.
        name: human-readable name shown in the UI / used for
            lookup.  Unique across the system.
        description: optional long-form description.  Use
            :obj:`None` to explicitly clear the column.
        created_at: when the role was created.  Set by the repo.
    """

    id: UndefinedOr[str] = UNDEFINED
    name: UndefinedOr[str] = UNDEFINED
    description: UndefinedNoneOr[str] = UNDEFINED
    created_at: UndefinedOr[datetime] = UNDEFINED

    def visit(self, visitor: EntityVisitor):
        """Dispatch this role to ``visitor.visit_role``."""
        return visitor.visit_role(self)


@dataclass
class UserRoleMembershipEntity(AcceptsVisitor):
    """A single ``user#member_of@role`` edge, optionally with audit metadata.

    Attributes:
        user_id: id of the user that holds the membership.
        role_id: id of the role the user is a member of.
        granted_at: when the membership was created.  Optional --
            the raw SpiceDB tuple has no timestamp, so this is only
            populated by callers that look it up from an audit log.
    """

    user_id: UndefinedOr[str] = UNDEFINED
    role_id: UndefinedOr[str] = UNDEFINED
    granted_at: UndefinedOr[datetime] = UNDEFINED

    def visit(self, visitor: EntityVisitor):
        """Dispatch this membership to ``visitor.visit_user_role_membership``."""
        return visitor.visit_user_role_membership(self)


@dataclass(eq=True, frozen=True)
class RoleFilter:
    """Filter for "find me a role matching these properties".

    Every field is :data:`UndefinedOr` so callers can leave fields
    unset.  Set fields are AND-ed -- the filter matches a role only
    when every set field equals the role's value for that field.

    Frozen so the dataclass can be hashed (callers use it as a cache
    key in the service layer); ``eq=True`` is the dataclass default.

    Attributes:
        name: target role's name.  :obj:`UNDEFINED` means
            "don't filter on name".
        member_id: id of a user that must be a member of the role.
            :obj:`UNDEFINED` means "don't filter on membership".
    """

    name: UndefinedOr[str] = UNDEFINED
    member_id: UndefinedOr[str] = UNDEFINED

    def is_empty(self) -> bool:
        """Return ``True`` when no field is set.

        Useful for callers that want to short-circuit an empty
        filter (``select_by_filter`` returns ``None`` for an empty
        filter by design).
        """
        return is_undefined(self.name) and is_undefined(self.member_id)

    def set_fields(self) -> List[str]:
        """Return the names of the fields that are currently set.

        Stable order so callers can compare two filters'
        :meth:`set_fields` results.
        """
        out: List[str] = []
        if not is_undefined(self.name):
            out.append("name")
        if not is_undefined(self.member_id):
            out.append("member_id")
        return out


__all__ = [
    "RoleEntity",
    "UserRoleMembershipEntity",
    "RoleFilter",
]