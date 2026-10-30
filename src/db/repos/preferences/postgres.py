from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from src.api.other.undefined import UNDEFINED
from src.api.repos.preferences_repo import PreferencesRepoABC
from src.api.services.preferences_service import PreferenceScope
from src.db.database import Database
from src.db.entities.preferences import PreferencesEntity
from src.grpc_mod.converter.postgres_row_converter import (
    PostgresRowConverter,
)
from src.utils import parse_json_object


_SCOPE_COLUMNS: Dict[PreferenceScope, str] = {
    "user": "user_id",
    "shelf": "shelf_id",
    "role": "role_id",
}


class _UnknownScopeError(ValueError):
    """Raised when an unrecognised PreferenceScope reaches the repo."""


class PostgresPreferencesRepo(PreferencesRepoABC):
    """Postgres-backed storage for the unified preferences table.

    Args:
        db: raw :class:`Database` connection.
        to_postgres_row: shared :class:`PostgresRowConverter`
            used to project :class:`PreferencesEntity` instances
            into row dicts before SQL.  The same instance is
            passed to every other Postgres repo in the process,
            so the row shape stays consistent end-to-end.
        now: factory for the ``updated_at`` column; defaults to
            ``datetime.now(timezone.utc)``.
    """

    def __init__(
        self,
        db: Database,
        to_postgres_row: PostgresRowConverter,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._db = db
        self._to_postgres_row = to_postgres_row
        self._now: Callable[[], datetime] = now or (
            lambda: datetime.now(timezone.utc)
        )

    @staticmethod
    def _owner_column(scope: PreferenceScope) -> str:
        """Return the FK column name for scope."""
        try:
            return _SCOPE_COLUMNS[scope]
        except KeyError as exc:
            raise _UnknownScopeError(
                f"unsupported preference scope: {scope!r}"
            ) from exc

    async def read(
        self, scope: PreferenceScope, target_id: str
    ) -> Dict[str, Any]:
        """Read the stored blob; return {} when no row exists."""
        owner_col = self._owner_column(scope)
        row = await self._db.fetchrow(
            f"SELECT payload FROM preferences WHERE {owner_col} = $1",
            target_id,
        )
        if row is None:
            return {}
        return parse_json_object(row.get("payload"), default={}) or {}

    async def read_entity(
        self, scope: PreferenceScope, target_id: str
    ) -> Optional[PreferencesEntity]:
        """Read the full PreferencesEntity; None when no row exists."""
        owner_col = self._owner_column(scope)
        row = await self._db.fetchrow(
            f"""
            SELECT id, {owner_col} AS owner_id, payload, updated_at
            FROM preferences
            WHERE {owner_col} = $1
            """,
            target_id,
        )
        if row is None:
            return None
        payload = parse_json_object(row.get("payload"), default={})
        return PreferencesEntity(
            id=row.get("id") if row.get("id") is not None else UNDEFINED,
            scope=scope,
            **{owner_col: row.get("owner_id")},
            payload=payload,
            updated_at=row.get("updated_at"),
        )

    async def upsert(
        self,
        scope: PreferenceScope,
        target_id: str,
        blob: Dict[str, Any],
        updated_at: Optional[datetime] = None,
    ) -> None:
        """Upsert the row owned by (scope, target_id).

        Built on top of :meth:`upsert_entity` so the row shape is
        always projected by the visitor.  Callers wanting full
        control should construct a :class:`PreferencesEntity` and
        call :meth:`upsert_entity` directly.
        """
        ts = updated_at if updated_at is not None else self._now()
        # Resolve the owner column up front so an unrecognised
        # scope raises a clear ValueError before any entity is built.
        owner_col = self._owner_column(scope)
        entity = PreferencesEntity(
            scope=scope,
            **{owner_col: target_id},
            payload=blob,
            updated_at=ts,
        )
        await self.upsert_entity(entity)

    async def delete(
        self, scope: PreferenceScope, target_id: str
    ) -> bool:
        """Drop the row owned by (scope, target_id); returns true when a row existed."""
        owner_col = self._owner_column(scope)
        result = await self._db.execute(
            f"DELETE FROM preferences WHERE {owner_col} = $1",
            target_id,
        )
        # asyncpg returns the affected-row count as a status string
        # like DELETE 1; parse the trailing token.
        try:
            count = int(str(result).rsplit(" ", 1)[-1])
        except (AttributeError, ValueError):
            return False
        return count > 0

    async def upsert_entity(self, entity: PreferencesEntity) -> None:
        """Upsert using a fully-shaped entity.

        The PostgresRowConverter.visit_preferences call projects the
        entity to a row dict shaped exactly like
        {owner: <target_id>, payload: <json dump>, updated_at: <ts>}.
        Both that projection and the SQL run through one helper so
        the row shape only lives in one place.
        """
        row = entity.convert(self._to_postgres_row)
        await self._execute_row(entity.scope, row)

    async def _execute_row(
        self,
        scope: PreferenceScope,
        row: Dict[str, Any],
    ) -> None:
        """Run the upsert SQL for a row dict shaped by the visitor."""
        if scope in (UNDEFINED, None) or scope not in _SCOPE_COLUMNS:
            raise _UnknownScopeError(
                f"upsert requires a concrete scope; got {scope!r}"
            )
        owner_col = _SCOPE_COLUMNS[scope]
        target_id = row.get(owner_col)
        if not target_id:
            raise ValueError(
                f"row missing owner value for column {owner_col}"
            )
        payload = row.get("payload", "{}")
        ts = row.get("updated_at") or self._now()
        await self._db.execute(
            f"""
            INSERT INTO preferences ({owner_col}, payload, updated_at)
            VALUES ($1, $2::jsonb, $3)
            ON CONFLICT ({owner_col}) DO UPDATE
                SET payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
            """,
            target_id,
            payload,
            ts,
        )


