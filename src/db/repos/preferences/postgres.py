from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from src.api.other.undefined import UNDEFINED
from src.api.repos.preferences_repo import (
    PreferencesLimitExceeded,
    PreferencesRepoABC,
)
from src.api.services.preferences_service import PreferenceScope
from src.db.entities.preferences import PreferencesEntity
from src.db.table import TableABC
from src.grpc_mod.converter.postgres_row_converter import (
    PostgresRowConverter,
)
from src.utils import parse_json_object


_SCOPE_COLUMNS: Dict[PreferenceScope, str] = {
    "user": "user_id",
    "shelf": "shelf_id",
    "role": "role_id",
}

#: Default cap on the number of preference rows a single owner may hold.
DEFAULT_MAX_PREFERENCES = 50


class _UnknownScopeError(ValueError):
    """Raised when an unrecognised PreferenceScope reaches the repo."""


class PostgresPreferencesRepo(PreferencesRepoABC):
    """Postgres-backed storage for the unified preferences table.

    Rows are addressed by ``(scope, target_id, key)``.  ``key`` is an
    optional caller-chosen string defaulting to ``""``; the empty key
    is the legacy/default row, so scope+target-only callers are
    unaffected.

    Args:
        table: :class:`~src.db.table.TableABC` wrapper for the
            ``preferences`` table.  The wrapper already carries the
            table name, so the repo never hardcodes it.
        to_postgres_row: shared :class:`PostgresRowConverter`
            used to project :class:`PreferencesEntity` instances
            into row dicts before SQL.  The same instance is
            passed to every other Postgres repo in the process,
            so the row shape stays consistent end-to-end.
        now: factory for the ``updated_at`` column; defaults to
            ``datetime.now(timezone.utc)``.
        max_preferences: maximum number of rows a single owner
            (user, shelf or role) may hold.  Inserting a new key
            past this cap raises :class:`PreferencesLimitExceeded`.
    """

    def __init__(
        self,
        table: TableABC,
        to_postgres_row: PostgresRowConverter,
        now: Optional[Callable[[], datetime]] = None,
        max_preferences: int = DEFAULT_MAX_PREFERENCES,
    ) -> None:
        if max_preferences < 1:
            raise ValueError("max_preferences must be >= 1")
        self._table = table
        self._to_postgres_row = to_postgres_row
        self._max_preferences = max_preferences
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

    @staticmethod
    def _normalise_key(key: Optional[str]) -> str:
        """Treat UNDEFINED/None as the default empty key."""
        if key is None or key is UNDEFINED:
            return ""
        return str(key)

    async def read(
        self, scope: PreferenceScope, target_id: str, key: str = ""
    ) -> Dict[str, Any]:
        """Read the stored blob for (scope, target_id, key); {} when absent."""
        owner_col = self._owner_column(scope)
        row = await self._table.select_row(
            {owner_col: target_id, "key": self._normalise_key(key)},
            select="payload",
        )
        if row is None:
            return {}
        return parse_json_object(row.get("payload"), default={}) or {}

    async def read_entity(
        self, scope: PreferenceScope, target_id: str, key: str = ""
    ) -> Optional[PreferencesEntity]:
        """Read the full PreferencesEntity; None when no row exists."""
        owner_col = self._owner_column(scope)
        row = await self._table.select_row(
            {owner_col: target_id, "key": self._normalise_key(key)},
            select="*",
        )
        if row is None:
            return None
        payload = parse_json_object(row.get("payload"), default={})
        return PreferencesEntity(
            id=row.get("id") if row.get("id") is not None else UNDEFINED,
            scope=scope,
            key=row.get("key") if row.get("key") is not None else "",
            **{owner_col: row.get(owner_col)},
            payload=payload,
            updated_at=row.get("updated_at"),
        )

    async def upsert(
        self,
        scope: PreferenceScope,
        target_id: str,
        blob: Dict[str, Any],
        updated_at: Optional[datetime] = None,
        key: str = "",
    ) -> None:
        """Upsert the row owned by (scope, target_id, key).

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
            key=self._normalise_key(key),
            **{owner_col: target_id},
            payload=blob,
            updated_at=ts,
        )
        await self.upsert_entity(entity)

    async def delete(
        self, scope: PreferenceScope, target_id: str, key: str = ""
    ) -> bool:
        """Drop the row owned by (scope, target_id, key); true iff it existed."""
        owner_col = self._owner_column(scope)
        removed = await self._table.delete(
            {owner_col: target_id, "key": self._normalise_key(key)}
        )
        return bool(removed)

    async def upsert_entity(self, entity: PreferencesEntity) -> None:
        """Upsert using a fully-shaped entity.

        The PostgresRowConverter.visit_preferences call projects the
        entity to a row dict shaped exactly like
        {owner: <target_id>, key: <key>, payload: <json dump>,
        updated_at: <ts>}.  Both that projection and the SQL run
        through one helper so the row shape only lives in one place.
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
        key = self._normalise_key(row.get("key", ""))
        payload = row.get("payload", "{}")
        ts = row.get("updated_at") or self._now()
        table_name = self._table.name

        # The per-(owner, key) unique indexes are partial
        # (``WHERE <owner> IS NOT NULL``).  Postgres can only infer a
        # partial index from ON CONFLICT when the clause repeats its
        # predicate, so mirror it here.
        #
        # The INSERT ... SELECT also enforces the per-owner cap
        # atomically: a brand-new key is only inserted while the
        # owner is below max_preferences.  Existing keys always pass
        # through so updates are never blocked by the cap.  A new key
        # past the cap returns no row, which we surface as the limit
        # error.
        rows = await self._table.fetch(
            f"""
            WITH counts AS (
                SELECT
                    EXISTS (
                        SELECT 1 FROM {table_name}
                        WHERE {owner_col} = $1 AND key = $2
                    ) AS already_exists,
                    (
                        SELECT COUNT(*) FROM {table_name}
                        WHERE {owner_col} = $1
                    ) AS owner_total
            )
            INSERT INTO {table_name} ({owner_col}, key, payload, updated_at)
            SELECT $1, $2, $3::jsonb, $4
            FROM counts
            WHERE counts.already_exists OR counts.owner_total < $5
            ON CONFLICT ({owner_col}, key) WHERE {owner_col} IS NOT NULL
            DO UPDATE
                SET payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
            RETURNING key
            """,
            target_id,
            key,
            payload,
            ts,
            self._max_preferences,
        )
        if not rows:
            raise PreferencesLimitExceeded(
                "maximum number of preferences reached for "
                f"{scope} {target_id!r} "
                f"(max {self._max_preferences})"
            )
