"""Postgres-backed implementation of :class:`~src.api.repos.user_repo.UserRepoABC`.

The repo is a thin wrapper over ``auth.user`` (renamed from
``public.users`` by the ``20260807-auth-schema`` migration).  It
deliberately performs no permission or business validation: user
lifecycle concerns belong to the service layer
(:class:`src.services.user_service.UserServiceImpl`).

The Discord identity (``discord_id`` + ``discriminator``) used to
live on the user row; the auth schema moved it to
``auth.third_party``.  :class:`UserPostgresRepo` therefore strips
those columns from INSERT/UPDATE payloads and refuses methods that
require a *filtered* Discord lookup (e.g.
:meth:`select_by_discord_id`) -- callers that need to search by
Discord id should switch to
:class:`src.db.repos.user.user_auth_postgres.PostgresUserAuthRepoImpl`.

Read methods (:meth:`select`, :meth:`fetch_users_by_ids`,
:meth:`search`) however resolve the linked Discord id / discriminator
via a correlated subquery on ``auth.third_party`` so the ``User``
gRPC message carries the real Discord snowflake instead of the proto
default ``0``.
"""

from __future__ import annotations

from typing import List, Optional

from asyncpg import Record

from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED
from src.api.repos.user_repo import UserRepoABC
from src.db.entities import FilterUser, UserEntity
from src.db.table import TableABC
from src.grpc_mod.converter.postgres_row_converter import (
    PostgresRowConverter,
)
from src.utils import asdict, drop_undefined, translate_unique_violation
from src.utils.logging import logging_provider as default_logging_provider


# ``discord_id`` and ``discriminator`` moved to ``auth.third_party``
# in the auth-schema migration.  Keep them out of the INSERT/UPDATE
# payload so the SQL doesn't reference columns that no longer exist
# on ``auth.user``.
_FIELDS_NOT_ON_AUTH_USER = frozenset({"discord_id", "discriminator"})


class UserPostgresRepo(UserRepoABC):
    """Provides an implementation using Postgres (more or less - other systems are not tested yet)
    """

    _returning = "id, avatar, username, email, display_name, type"

    # ``auth.third_party`` owns the Discord identity after the
    # auth-schema migration.  Correlated scalar subqueries keep the
    # join to at most one row per user (a user may have zero linked
    # providers, and the schema does not enforce one row per
    # provider), so the batch read stays a single round trip without
    # duplicating user rows.  ``discord_id`` is cast to ``bigint`` to
    # match :class:`UserEntity`; the ``provider = 'discord'`` filter
    # also excludes the google links whose ids are not numeric.
    _discord_id_subquery = (
        "(SELECT tp.provider_user_id::bigint "
        "FROM auth.third_party tp "
        "WHERE tp.user_id = u.id AND tp.provider = 'discord' "
        "ORDER BY tp.created_at, tp.id LIMIT 1)"
    )
    _discriminator_subquery = (
        "(SELECT tp.extra_fields->>'discriminator' "
        "FROM auth.third_party tp "
        "WHERE tp.user_id = u.id AND tp.provider = 'discord' "
        "ORDER BY tp.created_at, tp.id LIMIT 1)"
    )

    @property
    def _joined_returning(self) -> str:
        """Select list for reads that need the linked Discord identity."""
        return (
            "u.id AS id, u.avatar AS avatar, u.username AS username, "
            "u.email AS email, u.display_name AS display_name, "
            "u.type AS type, "
            f"{self._discord_id_subquery} AS discord_id, "
            f"{self._discriminator_subquery} AS discriminator"
        )

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

    @translate_unique_violation(fields=("email", "username"))
    async def insert(self, user: UserEntity) -> UserEntity:
        """Insert a new user and return the created entity with ID."""
        records = await self._table.insert(
            self._strip_moved_fields(user.convert(self._to_postgres_row)),
            returning=self._returning,
        )
        if not records:
            raise RuntimeError("Failed to insert user; no row returned")
        return self._from_record(records[0])

    @translate_unique_violation(fields=("email", "username"))
    async def update(self, user: UserEntity) -> UserEntity:
        """Update an existing user.

        Only fields that are not :obj:`~src.api.undefined.UNDEFINED` are
        written; explicit ``None`` clears the column.
        """
        if not user.id:
            raise ValueError("User ID is required for update operation")

        set_values = self._strip_moved_fields(user.convert(self._to_postgres_row))
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

    async def select(self, filter: FilterUser) -> Optional[UserEntity]:
        """Fetch the first user matching every set field of ``filter``."""
        if filter.is_empty():
            return None
        where: dict[str, object] = {}
        if filter.user_id is not UNDEFINED:
            where["id"] = filter.user_id
        if filter.username is not UNDEFINED:
            where["username"] = filter.username

        # A raw SELECT (instead of ``select_row``) so the Discord
        # identity from ``auth.third_party`` is resolved alongside the
        # user row -- ``select_row`` only knows the plain table.
        keys = list(where)
        clause = " AND ".join(
            f"u.{key} = ${index + 1}" for index, key in enumerate(keys)
        )
        records = await self._table.fetch(
            f"SELECT {self._joined_returning} "
            f"FROM {self._table.name} u WHERE {clause} LIMIT 1",
            *[where[key] for key in keys],
        )
        if not records:
            return None
        return self._from_record(records[0])

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

    async def fetch_users_by_ids(self, ids: List[str]) -> List[UserEntity]:
        """Fetch many users by id in one query.

        Uses ``id = ANY($1)`` so the whole batch is a single round
        trip (no N+1).  Unknown ids are skipped and the result is
        re-ordered to the input order; duplicated input ids are
        collapsed.
        """
        if not ids:
            return []
        wanted: List[str] = []
        seen: set[str] = set()
        for raw in ids:
            user_id = str(raw)
            if user_id in seen:
                continue
            seen.add(user_id)
            wanted.append(user_id)

        sql = (
            f"SELECT {self._joined_returning} "
            f"FROM {self._table.name} u "
            "WHERE u.id = ANY($1)"
        )
        records = await self._table.fetch(sql, wanted) or []
        by_id = {
            str(record["id"]): self._from_record(record) for record in records
        }
        return [by_id[user_id] for user_id in wanted if user_id in by_id]

    async def search(self, query: str, limit: int = 10) -> List[UserEntity]:
        """Search ``auth.user`` by partial id, fuzzy username or exact email.

        Email-shaped queries (containing ``@``) are matched exactly;
        everything else matches the id as a substring and ranks the
        username / display name by trigram similarity.  The queries
        use ``pg_trgm`` (``similarity``), which the initial schema
        enables.
        """
        term = (query or "").strip()
        if not term or limit <= 0:
            return []

        if "@" in term:
            # Exact match only: an email lookup must not fuzzily
            # return unrelated addresses.
            sql = (
                f"SELECT {self._joined_returning} "
                f"FROM {self._table.name} u "
                "WHERE u.email = $1 LIMIT $2"
            )
            records = await self._table.fetch(sql, term, limit) or []
            return [self._from_record(record) for record in records]

        # Escape LIKE wildcards in the user-supplied term so a
        # literal ``%`` or ``_`` doesn't widen the match.
        escaped = (
            term.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        sql = (
            f"SELECT {self._joined_returning} "
            f"FROM {self._table.name} u "
            "WHERE u.id ILIKE $2 ESCAPE '\\' "
            "OR u.username ILIKE $2 ESCAPE '\\' "
            "OR u.display_name ILIKE $2 ESCAPE '\\' "
            "OR similarity(u.username, $1) > 0.1 "
            "OR similarity(u.display_name, $1) > 0.1 "
            "ORDER BY COALESCE("
            "GREATEST(similarity(u.username, $1), similarity(u.display_name, $1)), 0"
            ") DESC, u.username ASC NULLS LAST "
            "LIMIT $3"
        )
        records = await self._table.fetch(sql, term, pattern, limit) or []
        return [self._from_record(record) for record in records]

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
        and ``discriminator`` are only present on read paths that use
        :attr:`_joined_returning` (resolved from ``auth.third_party``);
        the plain :attr:`_returning` used for INSERT/UPDATE does not
        include them, so they default to ``None`` for those callers.
        """
        data = dict(record)
        data.setdefault("discord_id", None)
        data.setdefault("discriminator", None)
        return UserEntity(**data)


__all__ = ["UserPostgresRepo"]
