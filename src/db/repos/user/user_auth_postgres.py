"""Postgres-backed implementation of :class:`UserAuthRepoABC`.

The repo is a thin wrapper over the four auth tables set up by
the ``20260807-auth-schema`` migration.  No permission or business
validation lives here -- that belongs to
:class:`src.services.user_auth_service.UserAuthServiceImpl`.

Tables:

* ``auth.user``       -- one row per user.
* ``auth.password``   -- one row per user (PK is ``user_id``).
* ``auth.passkey``    -- many rows per user.
* ``auth.third_party`` -- one row per linked provider; ``(provider,
  provider_user_id)`` is unique.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import replace
from typing import List, Optional

from asyncpg import Record

from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.repos.user_auth_repo import UserAuthRepoABC
from src.api.services.user_service import UserFilter
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.password import PasswordEntity
from src.db.entities.user.third_party import ThirdPartyEntity, ThirdPartyFilter
from src.db.entities.user.user_auth import UserAuthEntity
from src.db.table import TableABC
from src.utils.logging import logging_provider as default_logging_provider


class PostgresUserAuthRepoImpl(UserAuthRepoABC):
    """Postgres implementation of the auth storage contract.

    Args:
        user_table: the ``auth.user`` :class:`TableABC`.
        password_table: the ``auth.password`` :class:`TableABC`.
        passkey_table: the ``auth.passkey`` :class:`TableABC`.
        third_party_table: the ``auth.third_party``
            :class:`TableABC`.
        logging_provider: optional logging factory.  Uses the
            project default when ``None``.
    """

    _user_returning = "id, avatar, username, email, type"
    _password_returning = "user_id, password_hash, created_at, updated_at"
    _passkey_returning = (
        "id, user_id, credential_id, public_key, sign_count, "
        "transports, aaguid, backup_eligible, backup_state, "
        "user_verified, friendly_name, created_at, last_used_at, "
        "revoked_at"
    )
    _third_party_returning = (
        "id, user_id, provider, provider_user_id, extra_fields, created_at"
    )

    # Column whitelists.  Explicit dicts (not ``asdict(entity)``) so
    # that future dataclass fields land here only after someone
    # explicitly adds them -- an unknown field sent to the
    # INSERT/UPDATE statement would crash the SQL builder.
    _user_columns: tuple[str, ...] = (
        "avatar",
        "username",
        "email",
        "type",
    )
    _user_update_columns: tuple[str, ...] = (
        "avatar",
        "username",
        "email",
        "type",
    )
    _password_columns: tuple[str, ...] = (
        "password_hash",
        "created_at",
        "updated_at",
    )
    _passkey_columns: tuple[str, ...] = (
        "user_id",
        "credential_id",
        "public_key",
        "sign_count",
        "transports",
        "aaguid",
        "backup_eligible",
        "backup_state",
        "user_verified",
        "friendly_name",
        "created_at",
        "last_used_at",
        "revoked_at",
    )
    _third_party_columns: tuple[str, ...] = (
        "user_id",
        "provider",
        "provider_user_id",
        "extra_fields",
        "created_at",
    )

    def __init__(
        self,
        user_table: TableABC,
        password_table: TableABC,
        passkey_table: TableABC,
        third_party_table: TableABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._user_table = user_table
        self._password_table = password_table
        self._passkey_table = passkey_table
        self._third_party_table = third_party_table
        self.log = (logging_provider or default_logging_provider)(
            __name__, self
        )

    async def insert(self, user: UserAuthEntity) -> UserAuthEntity:
        """Insert a new user; the assigned id is stamped back on the entity.

        ``third_parties`` carried on the entity are written too via
        :meth:`insert_third_party` after the user row lands.
        """
        records = await self._user_table.insert(
            self._user_row_from_entity(user),
            returning=self._user_returning,
        )
        if not records:
            raise RuntimeError("Failed to insert user; no row returned")
        created = self._user_from_record(records[0])

        for tp in user.third_parties:
            await self.insert_third_party(
                replace(tp, user_id=created.id)
            )

        full = await self.select(UserFilter(user_id=str(created.id)))
        return full if full is not None else created

    async def select(
        self, filter: UserFilter
    ) -> Optional[UserAuthEntity]:
        """Fetch the first user matching every set field of `filter`."""
        if filter.is_empty():
            return None
        conditions: list[str] = []
        values: list[object] = []

        if filter.user_id:
            values.append(filter.user_id)
            conditions.append(f"u.id = ${len(values)}")
        if filter.email:
            values.append(filter.email)
            conditions.append(f"u.email = ${len(values)}")
        if filter.discord_id:
            values.append(str(filter.discord_id))
            conditions.append(
                f"EXISTS (SELECT 1 FROM auth.third_party tp "
                f"WHERE tp.user_id = u.id "
                f"AND tp.provider = 'discord' "
                f"AND tp.provider_user_id = ${len(values)})"
            )

        where = " AND ".join(conditions)
        records = await self._user_table.fetch(
            f"SELECT {self._user_returning} "
            f"FROM auth.user u WHERE {where} LIMIT 1",
            *values,
        )
        if not records:
            return None
        user = self._user_from_record(records[0])
        user.third_parties = await self._third_parties_for_user(str(user.id))
        return user

    async def update(self, user: UserAuthEntity) -> UserAuthEntity:
        """Persist partial updates to an existing user."""
        if not user.id:
            raise ValueError("User ID is required for update operation")
        user_id = str(user.id)

        # Build the SET clause only from the updatable columns --
        # the ``id`` and ``third_parties`` columns are managed
        # separately.  ``None`` is preserved as an explicit clear.
        set_values: dict[str, object] = {}
        for field_name in self._user_update_columns:
            value = getattr(user, field_name, UNDEFINED)
            if value is not UNDEFINED:
                set_values[field_name] = value
        if set_values:
            await self._user_table.update(
                set=set_values,
                where={"id": user_id},
                returning=self._user_returning,
            )

        if user.third_parties:
            existing = await self._third_parties_for_user(user_id)
            existing_by_provider = {tp.provider: tp for tp in existing}
            for tp in user.third_parties:
                if tp.provider in existing_by_provider:
                    await self._third_party_table.update(
                        set={"extra_fields": tp.serialised_extras},
                        where={
                            "id": str(
                                existing_by_provider[tp.provider].id
                            )
                        },
                        returning=self._third_party_returning,
                    )
                else:
                    await self.insert_third_party(
                        replace(tp, user_id=user_id)
                    )

        updated = await self.select(UserFilter(user_id=user_id))
        if updated is None:
            raise ValueError(f"User not found: {user_id}")
        return updated

    async def upsert_password(
        self, password: PasswordEntity
    ) -> PasswordEntity:
        """Upsert the user's password row."""
        # ``user_id`` is the PK; it's set on the entity by the
        # service and the column whitelist deliberately omits it.
        values: dict[str, object] = {
            "password_hash": password.password_hash,
            "created_at": password.created_at or _dt.datetime.now(),
            "updated_at": _dt.datetime.now(),
        }
        record = await self._password_table.upsert(
            values, returning=self._password_returning
        )
        first = record[0] if isinstance(record, list) else record
        if first is None or isinstance(first, str):
            raise RuntimeError("Failed to upsert password; no row returned")
        return self._password_from_record(first)

    async def find_password(
        self, user_id: str
    ) -> Optional[PasswordEntity]:
        """Return the user's password row, or ``None``."""
        record = await self._password_table.fetch_by_id(
            user_id, select=self._password_returning
        )
        if record is None:
            return None
        return self._password_from_record(record)

    async def insert_passkey(
        self, passkey: PasskeyEntity
    ) -> PasskeyEntity:
        """Insert a new passkey and return the persisted entity."""
        records = await self._passkey_table.insert(
            self._passkey_row_from_entity(passkey),
            returning=self._passkey_returning,
        )
        if not records:
            raise RuntimeError("Failed to insert passkey; no row returned")
        return self._passkey_from_record(records[0])

    async def find_passkey(
        self, credential_id: bytes
    ) -> Optional[PasskeyEntity]:
        """Find a passkey by its WebAuthn credential id."""
        record = await self._passkey_table.select_row(
            where={"credential_id": credential_id},
            select=self._passkey_returning,
        )
        if record is None:
            return None
        return self._passkey_from_record(record)

    async def find_passkey_by_id(
        self, passkey_id: str
    ) -> Optional[PasskeyEntity]:
        """Find a passkey by its server-assigned id."""
        record = await self._passkey_table.fetch_by_id(
            passkey_id, select=self._passkey_returning
        )
        if record is None:
            return None
        return self._passkey_from_record(record)

    async def list_passkeys(
        self, user_id: str, include_revoked: bool = False
    ) -> List[PasskeyEntity]:
        """List a user's passkeys, revoked hidden by default."""
        if include_revoked:
            records = await self._passkey_table.fetch(
                f"SELECT {self._passkey_returning} FROM auth.passkey "
                f"WHERE user_id = $1 ORDER BY id",
                user_id,
            )
        else:
            records = await self._passkey_table.fetch(
                f"SELECT {self._passkey_returning} FROM auth.passkey "
                f"WHERE user_id = $1 AND revoked_at IS NULL ORDER BY id",
                user_id,
            )
        return [self._passkey_from_record(r) for r in (records or [])]

    async def update_passkey_sign_count(
        self,
        passkey_id: str,
        new_sign_count: int,
    ) -> PasskeyEntity:
        """Bump the sign counter; rejects a non-monotonic counter."""
        current = await self.find_passkey_by_id(passkey_id)
        if current is None:
            raise KeyError(f"passkey not found: {passkey_id}")
        if new_sign_count <= current.sign_count:
            raise ValueError(
                f"new_sign_count ({new_sign_count}) must be > "
                f"current ({current.sign_count})"
            )
        record = await self._passkey_table.update(
            set={
                "sign_count": new_sign_count,
                "last_used_at": _dt.datetime.now(),
            },
            where={"id": passkey_id},
            returning=self._passkey_returning,
        )
        if record is None or isinstance(record, (list, str)):
            raise KeyError(f"passkey not found: {passkey_id}")
        return self._passkey_from_record(record)

    async def revoke_passkey(self, passkey_id: str) -> PasskeyEntity:
        """Stamp ``revoked_at`` on the passkey.

        Idempotent -- keeps the original revoke timestamp on a
        second call.
        """
        existing = await self.find_passkey_by_id(passkey_id)
        if existing is None:
            raise KeyError(f"passkey not found: {passkey_id}")
        if existing.revoked_at is not None:
            return existing
        record = await self._passkey_table.update(
            set={"revoked_at": _dt.datetime.now()},
            where={"id": passkey_id},
            returning=self._passkey_returning,
        )
        if record is None or isinstance(record, (list, str)):
            raise KeyError(f"passkey not found: {passkey_id}")
        return self._passkey_from_record(record)

    async def insert_third_party(
        self, third: ThirdPartyEntity
    ) -> ThirdPartyEntity:
        """Insert a third-party link."""
        records = await self._third_party_table.insert(
            self._third_party_row_from_entity(third),
            returning=self._third_party_returning,
        )
        if not records:
            raise RuntimeError(
                "Failed to insert third_party; no row returned"
            )
        return self._third_party_from_record(records[0])

    async def find_third_party(
        self, filter: ThirdPartyFilter
    ) -> List[ThirdPartyEntity]:
        """Return every third-party link matching `filter`."""
        if filter.is_empty():
            return []
        conditions: list[str] = []
        values: list[object] = []

        if not is_undefined(filter.id):
            values.append(filter.id)
            conditions.append(f"id = ${len(values)}")
        if not is_undefined(filter.user_id):
            values.append(filter.user_id)
            conditions.append(f"user_id = ${len(values)}")
        if not is_undefined(filter.provider):
            values.append(filter.provider)
            conditions.append(f"provider = ${len(values)}")
        if not is_undefined(filter.provider_user_id):
            values.append(filter.provider_user_id)
            conditions.append(f"provider_user_id = ${len(values)}")

        where = " AND ".join(conditions)
        records = await self._third_party_table.fetch(
            f"SELECT {self._third_party_returning} "
            f"FROM auth.third_party WHERE {where} ORDER BY id",
            *values,
        )
        return [self._third_party_from_record(r) for r in (records or [])]

    async def delete_third_party(self, third_party_id: str) -> bool:
        """Delete a third-party link by its id.

        Returns ``True`` when a row was removed, ``False`` when the
        id was unknown.
        """
        deleted = await self._third_party_table.delete(
            where={"id": third_party_id},
            returning="id",
        )
        return bool(deleted)

    async def _third_parties_for_user(
        self, user_id: str
    ) -> List[ThirdPartyEntity]:
        """Return every :class:`ThirdPartyEntity` linked to `user_id`."""
        records = await self._third_party_table.fetch(
            f"SELECT {self._third_party_returning} "
            f"FROM auth.third_party WHERE user_id = $1 ORDER BY id",
            user_id,
        )
        return [self._third_party_from_record(r) for r in (records or [])]

    @staticmethod
    def _user_from_record(record: Record) -> UserAuthEntity:
        """Map an asyncpg record into a :class:`UserAuthEntity`."""
        return UserAuthEntity(**dict(record))

    @staticmethod
    def _password_from_record(record: Record) -> PasswordEntity:
        """Map an asyncpg record into a :class:`PasswordEntity`."""
        return PasswordEntity(**dict(record))

    @staticmethod
    def _passkey_from_record(record: Record) -> PasskeyEntity:
        """Map an asyncpg record into a :class:`PasskeyEntity`."""
        return PasskeyEntity(**dict(record))

    @staticmethod
    def _third_party_from_record(record: Record) -> ThirdPartyEntity:
        """Map an asyncpg record into a :class:`ThirdPartyEntity`."""
        return ThirdPartyEntity(**dict(record))

    @classmethod
    def _user_row_from_entity(
        cls, user: UserAuthEntity
    ) -> dict[str, object]:
        """Build an INSERT-shaped row from the ``auth.user`` columns.

        Walks the explicit column whitelist rather than
        ``asdict(user)`` so that future dataclass fields land in
        this helper only after someone has confirmed the column
        exists.  An unknown field would otherwise crash the SQL
        builder at runtime.
        """
        row: dict[str, object] = {}
        for field_name in cls._user_columns:
            value = getattr(user, field_name, UNDEFINED)
            if value is not UNDEFINED:
                row[field_name] = value
        return row

    @classmethod
    def _passkey_row_from_entity(
        cls, passkey: PasskeyEntity
    ) -> dict[str, object]:
        """Build an INSERT-shaped row from the ``auth.passkey`` columns."""
        row: dict[str, object] = {}
        for field_name in cls._passkey_columns:
            value = getattr(passkey, field_name, UNDEFINED)
            if value is not UNDEFINED:
                row[field_name] = value
        return row

    @classmethod
    def _third_party_row_from_entity(
        cls, third: ThirdPartyEntity
    ) -> dict[str, object]:
        """Build an INSERT-shaped row from the ``auth.third_party`` columns."""
        row: dict[str, object] = {}
        for field_name in cls._third_party_columns:
            value = getattr(third, field_name, UNDEFINED)
            if value is not UNDEFINED:
                row[field_name] = value
        # ``extra_fields`` lands as the JSON-serialised string when
        # non-empty; the column is JSONB and accepts the string.
        if third.extra_fields:
            row["extra_fields"] = third.serialised_extras
        return row


__all__ = ["PostgresUserAuthRepoImpl"]
