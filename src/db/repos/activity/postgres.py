"""Postgres-backed implementation of :class:`ActivityRepoABC`.

The repo is a thin wrapper over the ``activity`` table created by
the ``20260707-create-activity-log`` migration.  It deliberately
performs no permission or business validation; authorisation belongs
to the service layer.

Directory subtree expansion is delegated to the injected
:class:`DirectoryRepoABC` via :meth:`DirectoryRepoABC.resolve_subtree`,
so the activity repo never has to know about SpiceDB or
:class:`UserContext`.  ``src.main` wires the directory repo in at
construction.

WHERE clauses are built via :class:`WhereClause` and rendered through
the table's bound :class:`SqlBuilderABC`; the repo never touches
placeholder syntax.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from asyncpg import Record

from src.api.activity import ActivityRepoABC
from src.api.types import LoggingProvider
from src.api.undefined import UNDEFINED, is_undefined
from src.db.entities.activity import (
    ActivityEntity,
    ActivityScore,
    FilterActivity,
)
from src.db.repos.activity.strategies import get_strategy
from src.api.directory_repo import DirectoryFacade
from src.db.sql_builders import SqliteSqlBuilder, WhereClause, WherePair
from src.db.table import TableABC
from src.utils import asdict, drop_undefined, logging_provider as default_logging_provider


class PostgresActivityRepo(ActivityRepoABC):
    """Postgres implementation of the activity-log storage contract."""

    _returning = 'id, actor_id, accessed_as, action, note_id, directory_id, role_id, at, metadata'

    def __init__(
        self,
        table: TableABC[List[Record]],
        directory_repo: Optional[DirectoryFacade] = None,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        """Initialise the repo.

        Args:
            table: low-level table wrapper for the ``activity`` table.
            directory_repo: directory repo used by
                :meth:`get_activities` / :meth:`get_most_used` to
                expand ``filter.directory_id`` into the full set of
                note / directory ids reachable from that root.  When
                ``None``, ``.set_directory(...)`` queries raise
                :class:`ValueError`.
            logging_provider: optional logger factory; falls back to
                :func:`src.utils.logging_provider`.
        """
        self._table = table
        self._directory_repo = directory_repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    async def get_activities(
        self,
        filter: FilterActivity,
    ) -> List[ActivityEntity]:
        """Return activity history matching ``filter``.

        Raises:
            ValueError: if ``filter.mode`` is not ``"history"``.
        """
        if filter.mode != "history":
            raise ValueError(
                "get_activities requires filter.mode == 'history'; "
                "use get_most_used() for aggregate queries"
            )

        where = await self._build_where_clause(filter)
        staged = (
            self._table.builder.select_from(self._table.name)
            .columns(*self._returning.split(", "))
            .where_clause(where)
            .order_by("at DESC")
        )
        if not is_undefined(filter.limit):
            staged = staged.limit(int(filter.limit))  # type: ignore[arg-type]
        if not is_undefined(filter.offset):
            staged = staged.offset(int(filter.offset))  # type: ignore[arg-type]
        stmt = staged.build()
        records = await self._table.fetch(stmt.sql, *stmt.params)
        return [self._from_record(record) for record in records or []]

    async def get_most_used(
        self,
        filter: FilterActivity,
    ) -> List[ActivityScore]:
        """Return aggregate ranking matching ``filter``.

        Raises:
            ValueError: if ``filter.mode`` is not ``"most_used"``, or
                if the strategy lookup fails.
        """
        if filter.mode != "most_used":
            raise ValueError(
                "get_most_used requires filter.mode == 'most_used'; "
                "use get_activities() for history queries"
            )

        # get strategy which generates SQL for the most_used
        algorithm = "count" if filter.algorithm is UNDEFINED else filter.algorithm
        strategy = get_strategy(algorithm)
        dialect = "sqlite" if isinstance(self._table.builder, SqliteSqlBuilder) else "postgres"
        strat_sql = strategy.build(filter, None, None, dialect=dialect)
        # add WHERE to the strategy's SQL
        where = await self._build_where_clause(filter)
        if strat_sql.where:
            where = where.add_raw(strat_sql.where)
        staged = (
            self._table.builder.select_from(self._table.name)
            .columns(*strat_sql.select_columns.split(", "))
            .where_clause(where)
            .group_by(strat_sql.group_by)
            .order_by(f"{strat_sql.order_by}, note_id ASC")
        )

        # add optional LIMIT
        if not is_undefined(filter.limit):
            staged = staged.limit(int(filter.limit))  # type: ignore[arg-type]

        # generate SQL
        stmt = staged.build()

        records = await self._table.fetch(stmt.sql, *stmt.params)
        return [
            ActivityScore(note_id=str(record["note_id"]), score=float(record["score"]))
            for record in records or []
        ]

    async def add_activity(self, activity: ActivityEntity) -> ActivityEntity:
        """Insert a new activity row and return the persisted entity.

        Required checks:

        * ``action`` must be set.
        * Exactly one of ``note_id`` / ``directory_id`` / ``role_id``
          must be a concrete value, matching the action prefix.
          ``note_*`` -> ``note_id``; ``directory_*`` -> ``directory_id``;
          ``role_*`` -> ``role_id``.  The schema no longer enforces
          this via CHECK; the logger service validates first and the
          repo re-validates as a backstop.
        """
        if activity.action in (UNDEFINED, None):
            raise ValueError("activity.action is required")

        _validate_target_shape(activity)

        values = drop_undefined(asdict(activity))
        # Postgres' JSONB column does not accept a Python ``dict`` from
        # asyncpg; serialise ``metadata`` to a JSON string before
        # handing it to the table layer so the same row shape works
        # on both Postgres and SQLite.
        if "metadata" in values and not isinstance(values["metadata"], str):
            values["metadata"] = json.dumps(dict(values["metadata"]))

        records = await self._table.insert(values, returning=self._returning)
        if not records:
            raise ValueError("Failed to insert activity")
        return self._from_record(records[0])

    async def remove_activity_by_id(self, activity_id: str) -> None:
        """Delete the activity with the given id.

        Raises ``ValueError`` if the row doesn't exist so callers can
        distinguish "already gone" from "not found".
        """
        deleted = await self._table.delete(
            where={"id": activity_id},
            returning="id",
        )
        if not deleted:
            raise ValueError(f"activity not found: {activity_id}")

    async def edit_activity(self, activity: ActivityEntity) -> ActivityEntity:
        """Persist changes to an existing activity.

        The entity's ``id`` is required; ``at`` is never overwritten
        via this path.  Other concrete fields replace the persisted
        column.  :obj:`~src.api.undefined.UNDEFINED` fields are
        ignored; :obj:`None` explicitly clears the column.
        """
        if activity.id in (UNDEFINED, None):
            raise ValueError("activity.id is required for update")

        # convert to dict and remove id and at
        set_values = asdict(replace(activity, id=UNDEFINED, at=UNDEFINED))
        set_values = drop_undefined(set_values)

        if not set_values:
            current = await self._table.select_row(
                where={"id": activity.id},
                select=self._returning,
            )
            if not current:
                raise ValueError(f"activity not found: {activity.id}")
            return self._from_record(current)

        record = await self._table.update(
            set=set_values,
            where={"id": activity.id},
            returning=self._returning,
        )
        if not record:
            raise ValueError(f"activity not found: {activity.id}")
        return self._from_record(record)

    async def _build_where_clause(self, filter: FilterActivity) -> WhereClause:
        """Translate a :class:`FilterActivity` into a :class:`WhereClause`.

        The clause grows incrementally via
        :meth:`WhereClause.add_and` / :meth:`WhereClause.add_or` so
        each optional filter is one line.  Plain equality pairs
        render as ``column = $N``; ``None`` on a nullable column
        renders as ``column IS NULL``; the directory subtree expands
        into ``note_id IN (...) OR directory_id IN (...)``; the time
        window becomes ``at >= $N``.
        """
        clause = WhereClause.empty()

        for column, value in (
            ("note_id", filter.note_id),
            ("actor_id", filter.actor_id),
            ("accessed_as", filter.accessed_as),
            ("role_id", filter.role_id),
        ):
            pair = _eq_pair(column, value)
            if pair is not None:
                clause = clause.add_and(pair)

        action_pair = _action_pair(filter)
        if action_pair is not None:
            clause = clause.add_and(action_pair)

        if not is_undefined(filter.days) and filter.days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=filter.days)
            clause = clause.add_and(("at >= $N", cutoff))

        # The directory subtree expansion is the trickiest case: each
        # root is resolved into its subtree notes + directories, then
        # OR'd together so any activity inside any of the subtrees
        # matches.  Lives in an OR group with the rest of the clause
        # because we want (note_id IN any subtree's notes) OR
        # (directory_id IN any subtree's directories), not both.
        if not is_undefined(filter.directory_ids):
            if filter.directory_ids is None or len(filter.directory_ids) == 0:
                clause = clause.add_and(("directory_id", None))
            else:
                if self._directory_repo is None:
                    raise ValueError(
                        "activity repo has no directory_repo; "
                        "pass directory_repo to the constructor to enable "
                        ".set_directory(...) queries"
                    )
                or_pairs: List[WherePair] = []
                for root_id in filter.directory_ids:
                    subtree_notes, subtree_dirs = await self._directory_repo.resolve_subtree(
                        root_id
                    )
                    if subtree_notes:
                        or_pairs.append(("note_id", subtree_notes))
                    if subtree_dirs:
                        or_pairs.append(("directory_id", subtree_dirs))
                if or_pairs:
                    clause = clause.add_or(*or_pairs)

        return clause

    @staticmethod
    def _from_record(record: Record) -> ActivityEntity:
        """Convert a full ``activity`` row into the entity."""
        return ActivityEntity(**dict(record))


def _eq_pair(column: str, value: object) -> Optional[WherePair]:
    """Return a ``(column, value)`` pair, or ``None`` to skip the column.

    UNDEFINED means "ignore this column"; ``None`` means match IS
    NULL
    """
    if is_undefined(value):
        return None
    return (column, value)


def _action_pair(filter: FilterActivity) -> Optional[WherePair]:
    """Return the action pair, or ``None`` if no action filter is set.

    ``action`` (single value) renders as ``column = $N``.
    """
    if not is_undefined(filter.action):
        return ("action", filter.action)
    if not is_undefined(filter.action_set):
        return ("action", list(filter.action_set))
    return None


def _validate_target_shape(activity: ActivityEntity) -> None:
    """Reject rows whose target shape doesn't match ``action``.

    The schema no longer enforces the per-kind target invariant; the
    logger service validates first and the repo re-validates as a
    backstop for direct repo callers.
    """
    action = activity.action
    if action in (UNDEFINED, None):
        # The earlier check already raised; this branch is unreachable
        # but keeps the type-checker happy.
        return

    note_set = activity.note_id not in (UNDEFINED, None)
    dir_set = activity.directory_id not in (UNDEFINED, None)
    role_set = activity.role_id not in (UNDEFINED, None)

    if action.startswith("note_"):
        if not note_set or dir_set or role_set:
            raise ValueError(
                f"activity action '{action}' requires note_id and rejects "
                f"directory_id / role_id"
            )
    elif action.startswith("directory_"):
        if not dir_set or note_set or role_set:
            raise ValueError(
                f"activity action '{action}' requires directory_id and rejects "
                f"note_id / role_id"
            )
    elif action in ("role_grant", "role_revoke", "role_change"):
        if not role_set or note_set or dir_set:
            raise ValueError(
                f"activity action '{action}' requires role_id and rejects "
                f"note_id / directory_id"
            )
    else:
        raise ValueError(f"unknown activity action: {action!r}")


__all__ = ["PostgresActivityRepo"]