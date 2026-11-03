"""Postgres-backed permission audit repo.

Thin wrapper over ``public.permission_audit`` (created by
``20261110-3-permission-audit``).  No permission checks live here.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from asyncpg import Record

from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.repos.permission_audit_repo import PermissionAuditRepoABC
from src.db.entities.permission_audit import (
    FilterPermissionAudit,
    PermissionAuditEntity,
)
from src.db.table import TableABC
from src.grpc_mod.converter.postgres_row_converter import PostgresRowConverter
from src.utils import parse_json_columns
from src.utils import logging_provider as default_logging_provider


class PostgresPermissionAuditRepo(PermissionAuditRepoABC):
    """Postgres implementation of :class:`PermissionAuditRepoABC`."""

    _returning = (
        "id, at, actor_id, accessed_as, action, resource_type, resource_id, "
        "relation, subject_type, subject_id, subject_relation, metadata"
    )

    def __init__(
        self,
        table: TableABC,
        to_postgres_row: Optional[PostgresRowConverter] = None,
        logging_provider: LoggingProvider | None = None,
    ) -> None:
        self._table = table
        self._to_postgres_row = to_postgres_row or PostgresRowConverter()
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    async def record_many(
        self,
        entries: Sequence[PermissionAuditEntity],
    ) -> None:
        """Insert every entry; an empty sequence is a no-op."""
        if not entries:
            return

        for entry in entries:
            values = entry.convert(self._to_postgres_row)
            if values.get("action") in (UNDEFINED, None):
                raise ValueError("permission audit action is required")
            await self._table.insert(values, returning="id")

    async def get(
        self,
        filter: FilterPermissionAudit,
    ) -> List[PermissionAuditEntity]:
        """Return matching rows newest-first."""
        conditions: list[str] = []
        values: list[object] = []

        for column, value in (
            ("resource_type", filter.resource_type),
            ("resource_id", filter.resource_id),
            ("action", filter.action),
        ):
            if is_undefined(value):
                continue
            values.append(value)
            conditions.append(f"{column} = ${len(values)}")

        if not is_undefined(filter.actor_id):
            if filter.actor_id is None:
                conditions.append("actor_id IS NULL")
            else:
                values.append(filter.actor_id)
                conditions.append(f"actor_id = ${len(values)}")

        if not is_undefined(filter.since) and filter.since is not None:
            values.append(filter.since)
            conditions.append(f"at >= ${len(values)}")

        where = " AND ".join(conditions) if conditions else "TRUE"
        sql = (
            f"SELECT {self._returning} FROM {self._table.name} "
            f"WHERE {where} ORDER BY at DESC"
        )
        if not is_undefined(filter.limit):
            values.append(int(filter.limit))  # type: ignore[arg-type]
            sql += f" LIMIT ${len(values)}"
        if not is_undefined(filter.offset):
            values.append(int(filter.offset))  # type: ignore[arg-type]
            sql += f" OFFSET ${len(values)}"

        records = await self._table.fetch(sql, *values)
        return [self._from_record(record) for record in records or []]

    def _from_record(self, record: Record) -> PermissionAuditEntity:
        data = parse_json_columns(record, ("metadata",), default={})
        return PermissionAuditEntity(
            id=str(data["id"]),
            at=data["at"],
            actor_id=data["actor_id"],
            accessed_as=data["accessed_as"],
            action=data["action"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            relation=data["relation"],
            subject_type=data["subject_type"],
            subject_id=data["subject_id"],
            subject_relation=data["subject_relation"],
            metadata=data["metadata"],
        )


__all__ = ["PostgresPermissionAuditRepo"]