"""Postgres-only storage for role metadata.

The ``roles`` table holds the role id, name, description and
created_at.  Role membership and all permission checks live in
:class:`src.services.role_service.RoleServiceImpl`, which talks to
:class:`PermissionRepoABC` directly.  This repo deliberately has no
SpiceDB dependency.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List, Optional

from asyncpg import Record

from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.other.user_context import UserContextABC
from src.api.repos.role_repo import RoleRepoABC
from src.db.entities.user.role import RoleEntity, RoleFilter
from src.db.table import TableABC
from src.grpc_mod.converter.postgres_row_converter import (
    PostgresRowConverter,
)
from src.utils import asdict, logging_provider as default_logging_provider


class PostgresRoleRepo(RoleRepoABC):
    """Persist role metadata in the Postgres ``roles`` table."""

    _returning = "id, name, description, created_at"

    def __init__(
        self,
        table: TableABC,
        to_postgres_row: Optional[PostgresRowConverter] = None,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._table = table
        # Internal knob: a shared PostgresRowConverter instance is injected from main.py.
        self._to_postgres_row = to_postgres_row or PostgresRowConverter()
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    async def create_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        if role.name in (UNDEFINED, None):
            raise ValueError("role.name is required")

        # description is the only optional column; UNDEFINED lets the default apply.
        values = role.convert(self._to_postgres_row)
        records = await self._table.insert(values, returning=self._returning)
        if not records:
            raise ValueError("Failed to create role")
        return self._from_record(records[0])

    async def update_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        if role.id in (UNDEFINED, None):
            raise ValueError("role.id is required")

        # Only name and description are mutable; the rest is dropped to keep the SET clause narrow.
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
        """Fetch roles by name.

        ``RoleFilter.member_id`` is intentionally a service-level
        concern: resolving it requires reading SpiceDB membership
        tuples.  The service strips it before calling this repo.
        """
        conditions: list[str] = []
        values: list[object] = []
        if not is_undefined(filter.name):
            values.append(filter.name)
            conditions.append(f"name = ${len(values)}")

        where = " AND ".join(conditions) if conditions else "TRUE"
        records = await self._table.fetch(
            f"SELECT {self._returning} FROM {self._table.name} WHERE {where}",
            *values,
        )
        return [self._from_record(record) for record in records or []]

    @staticmethod
    def _from_record(record: Record) -> RoleEntity:
        """Convert an asyncpg record into the role entity."""
        return RoleEntity(**dict(record))


__all__ = ["PostgresRoleRepo"]
