"""Postgres-backed implementation of :class:`UserActionRepoABC`.

The repo is a thin wrapper over the ``user_action`` table created by
the ``20260620-create-share-relation`` migration.  It deliberately
performs no permission or business validation: scheduling decisions
belong to the service layer.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List, Optional

from asyncpg import Record

from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, is_undefined
from src.api.repos.user_action_repo import UserActionRepoABC
from src.db.entities.user.user_action import (
    FilterUserAction,
    UserActionEntity,
)
from src.db.table import TableABC
from src.utils import asdict, drop_undefined, logging_provider as default_logging_provider


class UserActionPostgresRepo(UserActionRepoABC):
    """Postgres implementation of the user-action storage contract."""

    _returning = "id, user_id, action, execute_at, executed_at"

    def __init__(
        self,
        table: TableABC[List[Record]],
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._table = table
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def get_actions_by_user(self, user_id: str) -> List[UserActionEntity]:
        """Return every action targeting ``user_id``, ordered by ``execute_at``."""
        records = await self._table.fetch(
            f"SELECT {self._returning} FROM {self._table.name} "
            f"WHERE user_id = $1 ORDER BY execute_at ASC",
            user_id,
        )
        return [self._from_record(record) for record in records or []]

    async def get_actions(
        self,
        filter: FilterUserAction,
    ) -> List[UserActionEntity]:
        """Apply a generic filter and return matching actions.

        See :class:`FilterUserAction` for the per-field semantics.
        """
        conditions: list[str] = []
        values: list[object] = []

        def _eq(column: str, value: object) -> None:
            values.append(value)
            conditions.append(f"{column} = ${len(values)}")

        if filter.id is not UNDEFINED:
            _eq("id", filter.id)
        if filter.user_id is not UNDEFINED:
            _eq("user_id", filter.user_id)
        if filter.action is not UNDEFINED:
            _eq("action", filter.action)

        # ``None`` on ``executed_at`` -> IS NULL; concrete datetime -> ``<= value``.
        if filter.executed_at is None:
            conditions.append("executed_at IS NULL")
        elif filter.executed_at is not UNDEFINED:
            values.append(filter.executed_at)
            conditions.append(f"executed_at <= ${len(values)}")

        # concrete datetime on ``execute_at`` -> ``>= value``.
        if filter.execute_at is not UNDEFINED:
            values.append(filter.execute_at)
            conditions.append(f"execute_at >= ${len(values)}")

        where = " AND ".join(conditions) if conditions else "TRUE"
        records = await self._table.fetch(
            f"SELECT {self._returning} FROM {self._table.name} "
            f"WHERE {where} ORDER BY execute_at ASC",
            *values,
        )
        return [self._from_record(record) for record in records or []]

    async def add_action(self, action: UserActionEntity) -> UserActionEntity:
        """Insert a new action row and return the persisted entity."""
        # required fields
        if action.user_id in (UNDEFINED, None):
            raise ValueError("action.user_id is required")
        if action.action in (UNDEFINED, None):
            raise ValueError("action.action is required")
        if action.execute_at in (UNDEFINED, None):
            raise ValueError("action.execute_at is required")

        # drop UNDEFINED columns; explicit None is preserved and clears the column.
        values = drop_undefined(asdict(action))
        records = await self._table.insert(values, returning=self._returning)
        if not records:
            raise ValueError("Failed to insert user_action")
        return self._from_record(records[0])

    async def remove_action(self, action_id: str) -> None:
        """Delete the action with the given id.

        Raises ``ValueError`` if the row doesn't exist so callers can
        distinguish "already gone" from "not found".
        """
        deleted = await self._table.delete(
            where={"id": action_id},
            returning="id",
        )
        if not deleted:
            raise ValueError(f"user_action not found: {action_id}")

    async def update_action(self, action: UserActionEntity) -> UserActionEntity:
        """Persist changes to an existing action.

        The entity's ``id`` is required; other concrete fields replace
        the persisted column.  ``UNDEFINED`` fields are ignored,
        ``None`` explicitly clears the column.
        """
        if action.id in (UNDEFINED, None):
            raise ValueError("action.id is required for update")

        set_values = asdict(
            replace(
                action,
                id=UNDEFINED,
            )
        )
        # don't permit an empty SET clause
        set_values.pop("id", None)
        if not set_values:
            current = await self._table.select_row(
                where={"id": action.id},
                select=self._returning,
            )
            if not current:
                raise ValueError(f"user_action not found: {action.id}")
            return self._from_record(current)

        record = await self._table.update(
            set=set_values,
            where={"id": action.id},
            returning=self._returning,
        )
        if not record:
            raise ValueError(f"user_action not found: {action.id}")
        return self._from_record(record)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _from_record(record: Record) -> UserActionEntity:
        """Convert an asyncpg record into the entity."""
        return UserActionEntity(**dict(record))


__all__ = ["UserActionPostgresRepo"]