"""Composite repo for roles: Postgres metadata + SpiceDB membership edges.

* The ``roles`` Postgres table holds metadata (id, name, description,
  created_at).
* SpiceDB holds ``user#member_of@role`` tuples.

The repo stitches both sides so callers see a single
:class:`RoleRepoABC` surface.  Permission checks live in the service
layer; this repo only translates requests into storage operations.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List, Optional

from asyncpg import Record

from src.api import (
    ObjectRef,
    ObjectTypeEnum,
    PermissionRepoABC,
    Relationship,
    SubjectRef,
    UserContextABC,
)
from src.api.other.relationship import RoleRelationEnum
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, UndefinedOr, is_undefined
from src.api.other.user_context import UserContextABC as _Ctx
from src.api.repos.role_repo import RoleRepoABC
from src.db.entities.user.role import (
    RoleEntity,
    RoleFilter,
    UserRoleMembershipEntity,
)
from src.db.table import TableABC
from src.utils import asdict, logging_provider as default_logging_provider
from src.utils.dict_helper import drop_undefined


class SpicedbRoleRepo(RoleRepoABC):
    """Postgres-metadata + SpiceDB-membership role storage.

    Metadata goes through a Postgres ``roles`` table; membership edges
    go through the existing :class:`PermissionRepoABC` so they share
    the same SpiceDB client and consistency model as the rest of the
    permission system.
    """

    _returning = "id, name, description, created_at"

    def __init__(
        self,
        table: TableABC,
        permission_repo: PermissionRepoABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._table = table
        self._permission_repo = permission_repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    # ---- role metadata (Postgres) ---------------------------------------

    async def create_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        if role.name in (UNDEFINED, None):
            raise ValueError("role.name is required")

        # ``description`` is the only optional column; ``None`` clears
        # it, ``UNDEFINED`` lets Postgres default to NULL.  We pass
        # ``None`` through when it was explicitly set, otherwise let
        # the column default kick in by not including the key.
        values: dict[str, object] = {"name": role.name}
        if not is_undefined(role.description):
            values["description"] = role.description

        records = await self._table.insert(values, returning=self._returning)
        if not records:
            raise ValueError("Failed to create role")
        return self._from_record(records[0])

    async def update_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        if role.id in (UNDEFINED, None):
            raise ValueError("role.id is required")

        # only ``name`` and ``description`` are mutable; everything
        # else is dropped to avoid an empty SET clause
        set_values = asdict(
            replace(
                role,
                id=UNDEFINED,
                created_at=UNDEFINED,
            )
        )
        if not set_values:
            current = await self._table.select_row(
                where={"id": role.id},
                select=self._returning,
            )
            if not current:
                raise ValueError(f"Role not found: {role.id}")
            return self._from_record(current)

        record = await self._table.update(
            set=set_values,
            where={"id": role.id},
            returning=self._returning,
        )
        if not record:
            raise ValueError(f"Role not found: {role.id}")
        return self._from_record(record)

    async def delete_role(self, role_id: str, ctx: UserContextABC) -> None:
        if not role_id:
            raise ValueError("role_id is required")
        deleted = await self._table.delete(where={"id": role_id}, returning="id")
        if not deleted:
            raise ValueError(f"Role not found: {role_id}")
        # Membership edges (``role:<id>#member@user:<id>``) and
        # resource grants targeting this role (``note:N#reader@role:<id>``)
        # are intentionally left in place: SpiceDB does not have a
        # "delete a role" operation, and removing the Postgres row
        # does not cascade into SpiceDB.  The caller is responsible
        # for sweeping those tuples out of band (typically via
        # ``remove_user_from_role`` for every known member before
        # calling ``delete_role``).

    async def get_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> Optional[RoleEntity]:
        if not role_id:
            raise ValueError("role_id is required")
        record = await self._table.select_row(
            where={"id": role_id},
            select=self._returning,
        )
        if not record:
            return None
        return self._from_record(record)

    async def get_roles(
        self,
        filter: RoleFilter,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        conditions: list[str] = []
        values: list[object] = []

        def add_value_condition(column: str, operator: str, value: object) -> None:
            values.append(value)
            conditions.append(f"{column} {operator} ${len(values)}")

        if not is_undefined(filter.name):
            add_value_condition("name", "=", filter.name)

        # ``member_id`` requires walking SpiceDB to find role ids the
        # user belongs to; we then narrow the Postgres SELECT to those
        # ids.  When the user belongs to no roles, return [].
        if not is_undefined(filter.member_id):
            role_ids = await self.get_role_ids_for_user(str(filter.member_id), ctx)
            if not role_ids:
                return []
            placeholders = []
            for role_id in role_ids:
                values.append(role_id)
                placeholders.append(f"${len(values)}")
            conditions.append(f"id IN ({', '.join(placeholders)})")

        where = " AND ".join(conditions) if conditions else "TRUE"
        records = await self._table.fetch(
            f"SELECT {self._returning} FROM {self._table.name} WHERE {where}",
            *values,
        )
        return [self._from_record(record) for record in records or []]

    # ---- membership edges (SpiceDB) -------------------------------------

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
        # Membership is encoded as ``role#member@user`` -- the
        # ``member`` relation on ``role`` is what the ``role#member``
        # userset (referenced by every ``user | role#member`` subject
        # elsewhere in the schema) expands to.
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
        await self._permission_repo.delete(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
                relation=RoleRelationEnum.MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        )

    async def get_roles_for_user(
        self,
        user_id: str,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        if not user_id:
            raise ValueError("user_id is required")
        role_ids = await self.get_role_ids_for_user(user_id, ctx)
        if not role_ids:
            return []
        roles: list[RoleEntity] = []
        for role_id in role_ids:
            role = await self.get_role(role_id, ctx)
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
        # Membership edges live as ``role#member@user:<id>``; pull
        # every tuple whose resource is this role and whose relation
        # is ``member``.
        tuples = await self._permission_repo.lookup_relationships(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
                relation=RoleRelationEnum.MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, UNDEFINED),
            )
        )
        return [
            UserRoleMembershipEntity(
                user_id=str(t.subject.object_id) if not is_undefined(t.subject.object_id) else UNDEFINED,
                role_id=role_id,
                granted_at=UNDEFINED,
            )
            for t in tuples
            if str(t.subject.object_type) == ObjectTypeEnum.USER.value
        ]

    # ---- internals ------------------------------------------------------

    async def get_role_ids_for_user(
        self,
        user_id: str,
        ctx: UserContextABC,
    ) -> List[str]:
        """Return role ids the user is a member of.

        Membership is encoded as ``role#member@user``; to find the
        roles for a user we walk every ``role:*#member@user:<id>``
        tuple.  Used by both ``get_roles`` (when filtering by
        ``member_id``) and ``get_roles_for_user`` so they share the
        same SpiceDB walking code path.
        """
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

    @staticmethod
    def _from_record(record: Record) -> RoleEntity:
        """Convert an asyncpg record into the role entity."""
        return RoleEntity(**dict(record))


__all__ = ["SpicedbRoleRepo"]