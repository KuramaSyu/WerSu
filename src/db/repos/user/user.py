"""Postgres-backed implementation of :class:`UserRepoABC`.

The repo is a thin wrapper over ``auth.user`` (renamed from
``public.users`` by the ``20260807-auth-schema`` migration).  It
deliberately performs no permission or business validation: user
lifecycle concerns belong to the service layer
(:class:`src.services.user_service.UserServiceImpl`).

The Discord identity (``discord_id`` + ``discriminator``) used to
live on the user row; the auth schema moved it to
``auth.third_party``.  :class:`UserPostgresRepo` therefore strips
those columns from INSERT/UPDATE payloads and refuses methods that
require a Discord join (e.g. :meth:`select_by_discord_id`) --
callers that need Discord lookups should switch to
:class:`src.db.repos.user.user_auth_postgres.PostgresUserAuthRepoImpl`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from asyncpg import Record

from src.api.other.types import LoggingProvider
from src.db.entities import UserEntity
from src.db.table import TableABC
from src.utils import asdict, drop_undefined
from src.utils.logging import logging_provider as default_logging_provider


# ``discord_id`` and ``discriminator`` moved to ``auth.third_party``
# in the auth-schema migration.  Keep them out of the INSERT/UPDATE
# payload so the SQL doesn't reference columns that no longer exist
# on ``auth.user``.
_FIELDS_NOT_ON_AUTH_USER = frozenset({"discord_id", "discriminator"})


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

    _returning = "id, avatar, username, email, type"

    def __init__(
        self,
        table: TableABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._table = table
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    @staticmethod
    def _strip_moved_fields(payload: dict) -> dict:
        """Drop columns that moved to ``auth.third_party``.

        ``discord_id`` and ``discriminator`` were renamed off the
        user row by the auth-schema migration.  Strip them so the
        INSERT/UPDATE doesn't reference columns that no longer
        exist on ``auth.user``.
        """
        return {
            k: v
            for k, v in payload.items()
            if k not in _FIELDS_NOT_ON_AUTH_USER
        }

    async def insert(self, user: UserEntity) -> UserEntity:
        """Insert a new user and return the created entity with ID.

        UNDEFINED fields are dropped from the payload so the column
        defaults (``id`` -> ``uuidv7()``, ``type`` -> ``human``) apply.
        Discord identity fields are stripped -- they live on
        ``auth.third_party`` now.
        """
        records = await self._table.insert(
            self._strip_moved_fields(drop_undefined(asdict(user))),
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
        if not user.id:
            raise ValueError("User ID is required for update operation")

        set_values = self._strip_moved_fields(drop_undefined(asdict(user)))
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

        ``discord_id`` now lives on ``auth.third_party``; the
        legacy single-table upsert is no longer reachable from this
        repo.  Callers should drive the auth schema directly via
        :class:`src.db.repos.user.user_auth_postgres.PostgresUserAuthRepoImpl`.
        """
        raise NotImplementedError(
            "UserPostgresRepo.upsert is not supported after the auth-schema "
            "migration; use PostgresUserAuthRepoImpl instead."
        )

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
        """Select a user by ``discord_id``.

        ``discord_id`` lives on ``auth.third_party`` now, so the
        legacy single-table select is gone.  Callers should switch
        to :class:`PostgresUserAuthRepoImpl` for Discord lookups.
        """
        raise NotImplementedError(
            "UserPostgresRepo.select_by_discord_id is not supported after the "
            "auth-schema migration; use PostgresUserAuthRepoImpl instead."
        )

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
        string so no further conversion is required.  ``discord_id``
        and ``discriminator`` are filled with ``None`` because they
        no longer live on the user row -- callers that need them
        must go through the auth schema.
        """
        data = dict(record)
        data.setdefault("discord_id", None)
        data.setdefault("discriminator", None)
        return UserEntity(**data)


__all__ = ["UserRepoABC", "UserPostgresRepo"]
