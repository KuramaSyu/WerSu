"""Postgres-backed implementation of :class:`UserRepoABC`.

The repo is a thin wrapper over the ``users`` table created by the
``initial-schema`` migration.  It deliberately performs no
permission or business validation: user lifecycle concerns belong to
the service layer (:class:`src.services.user.UserService`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from asyncpg import Record

from src.api.types import LoggingProvider
from src.db.entities import UserEntity
from src.db.table import TableABC
from src.utils import asdict, drop_undefined
from src.utils.logging import logging_provider as default_logging_provider


class UserRepoABC(ABC):
    """Abstract user storage contract."""

    @abstractmethod
    async def insert(self, user: UserEntity) -> UserEntity:
        """Insert a new user and return the created entity with ID."""

    @abstractmethod
    async def update(self, user: UserEntity) -> UserEntity:
        """Update an existing user."""

    @abstractmethod
    async def upsert(self, user: UserEntity) -> UserEntity:
        """Insert or update a user based on discord_id."""

    @abstractmethod
    async def select(self, user_id: str) -> Optional[UserEntity]:
        """Select a user by ID."""

    @abstractmethod
    async def select_by_discord_id(self, discord_id: int) -> Optional[UserEntity]:
        """Select a user by discord_id."""

    @abstractmethod
    async def delete(self, user_id: str) -> bool:
        """Delete a user by ID."""


class UserPostgresRepo(UserRepoABC):
    """Provides an implementation using Postgres (more or less - other systems are not tested yet)
    """

    _returning = "id, discord_id, avatar, username, discriminator, email, type"

    def __init__(
        self,
        table: TableABC[List[Record]],
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._table = table
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    async def insert(self, user: UserEntity) -> UserEntity:
        """Insert a new user and return the created entity with ID.

        UNDEFINED fields are dropped from the payload so the column
        defaults (``id`` -> ``uuidv7()``, ``type`` -> ``human``) apply.
        """
        records = await self._table.insert(
            drop_undefined(asdict(user)),
            returning=self._returning,
        )
        if not records:
            raise RuntimeError("Failed to insert user; no row returned")
        return self._from_record(records[0])

    async def update(self, user: UserEntity) -> UserEntity:
        """Update an existing user.

        Only fields that are not :obj:`~src.api.undefined.UNDEFINED` are
        written; explicit ``None`` clears the column.
        """
        if user.id is None:
            raise ValueError("User ID is required for update operation")

        set_values = drop_undefined(asdict(user))
        set_values.pop("id", None)
        if not set_values:
            current = await self._table.select_row(
                where={"id": user.id},
                select=self._returning,
            )
            if not current:
                raise ValueError(f"User not found: {user.id}")
            return self._from_record(current)

        record = await self._table.update(
            set=set_values,
            where={"id": user.id},
            returning=self._returning,
        )
        if not record:
            raise ValueError(f"User not found: {user.id}")
        return self._from_record(record)

    async def upsert(self, user: UserEntity) -> UserEntity:
        """Insert or update a user based on ``discord_id``.

        ``id`` and ``type`` are intentionally excluded from the SET
        clause so the original uuid is preserved across updates and a
        Discord re-link cannot silently downgrade an account.
        """
        set_values = drop_undefined(asdict(user))
        set_values.pop("id", None)
        set_values.pop("type", None)
        if not set_values:
            if user.discord_id is None:
                raise ValueError(
                    "User discord_id is required for upsert operation"
                )
            existing = await self.select_by_discord_id(user.discord_id)
            if existing is None:
                raise ValueError(
                    f"User not found by discord_id: {user.discord_id}"
                )
            return existing

        records = await self._table.upsert(
            drop_undefined(asdict(user)),
            returning=self._returning,
        )
        if not records:
            raise RuntimeError("Failed to upsert user; no row returned")
        # ``upsert`` may return a list -- normalize to the first record.
        first = records[0] if isinstance(records, list) else records
        return self._from_record(first)

    async def select(self, user_id: str) -> Optional[UserEntity]:
        """Select a user by ID."""
        record = await self._table.select_row(
            where={"id": user_id},
            select=self._returning,
        )
        if record is None:
            return None
        return self._from_record(record)

    async def select_by_discord_id(self, discord_id: int) -> Optional[UserEntity]:
        """Select a user by discord_id."""
        record = await self._table.select_row(
            where={"discord_id": discord_id},
            select=self._returning,
        )
        if record is None:
            return None
        return self._from_record(record)

    async def delete(self, user_id: str) -> bool:
        """Delete a user by ID."""
        deleted = await self._table.delete(
            where={"id": user_id},
            returning="id",
        )
        return bool(deleted)

    @staticmethod
    def _from_record(record: Record) -> UserEntity:
        """Convert an asyncpg record into the entity.

        The ``type`` column comes back as the ``user_kind`` enum string;
        :class:`UserEntity` accepts the free-form :class:`UserTypeT`
        string so no further conversion is required.
        """
        return UserEntity(**dict(record))


__all__ = ["UserRepoABC", "UserPostgresRepo"]
