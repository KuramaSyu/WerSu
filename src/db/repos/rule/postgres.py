"""Postgres-backed implementation of :class:`RuleRepoABC`.

The repo is a thin wrapper over the ``rules`` table created by
the ``20260817-create-rules-table`` migration.  It deliberately
performs no permission or business validation; authorisation
belongs to the service layer.

JSONB columns (``condition`` and ``action_context``) are stored as
JSON strings because asyncpg's JSONB encoder does not accept raw
Python ``dict`` objects -- mirroring the pattern in
:class:`PostgresActivityRepo`.  On the way out the JSON string is
parsed back into a ``dict`` so the rest of the stack sees a
uniform ``Mapping`` shape.

The repo re-uses the same ``Table`` wrapper abstraction as every
other repo, so SQL composition stays in the existing
:class:`SqlBuilderABC` and dialect handling is transparent.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Dict, List, Optional

from asyncpg import Record

from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.repos.rule_repo import RuleRepoABC
from src.db.entities.rule import AttachedEntityType, RuleEntity
from src.db.sql_builders import WhereClause
from src.db.table import TableABC
from src.utils import asdict, drop_undefined, logging_provider as default_logging_provider


class PostgresRuleRepo(RuleRepoABC):
    """Postgres implementation of the rules storage contract."""

    _returning = (
        "id, event_type, attached_entity_type, attached_entity_id, "
        "condition, action_type, action_context, enabled, creator_id, "
        "created_at, updated_at"
    )

    def __init__(
        self,
        table: TableABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        """Initialise the repo.

        Args:
            table: low-level table wrapper for the ``rules`` table.
            logging_provider: optional logger factory; falls back to
                :func:`src.utils.logging_provider`.
        """
        self._table = table
        self.log = (logging_provider or default_logging_provider)(__name__, self)


    # ---- single-row CRUD ------------------------------------------------

    async def create_rule(self, rule: RuleEntity) -> RuleEntity:
        """Insert a new rule and return the persisted entity.

        Validates that ``event_type``, ``condition``,
        ``action_type``, ``action_context``, ``enabled`` and
        ``creator_id`` are all set before handing the row to the
        table wrapper.  ``id`` is left unset so the database
        generates the primary key.
        """
        for required in (
            "event_type",
            "condition",
            "action_type",
            "action_context",
            "enabled",
            "creator_id",
        ):
            if is_undefined(getattr(rule, required)):
                raise ValueError(f"rule.{required} is required")

        values = self._entity_to_insert_dict(rule)
        records = await self._table.insert(values, returning=self._returning)
        if not records:
            raise ValueError("failed to insert rule")
        return self._from_record(records[0])


    async def get_rule_by_id(self, rule_id: str) -> Optional[RuleEntity]:
        """Return the rule with ``rule_id``, or ``None`` if absent."""
        record = await self._table.fetch_by_id(
            rule_id, select=self._returning,
        )
        if not record:
            return None
        return self._from_record(record)


    async def update_rule(self, rule: RuleEntity) -> RuleEntity:
        """Persist changes to an existing rule.

        The ``id`` field is required and is stripped from the
        SET clause (it is the WHERE key).  ``created_at`` is
        never overwritten; ``updated_at`` is bumped by the DB
        via its column default when explicitly cleared, so we
        pass it as ``now()``.
        """
        if is_undefined(rule.id) or rule.id is None:
            raise ValueError("rule.id is required for update")

        set_values: Dict[str, Any] = {}
        # Walk every field except id / created_at and either
        # drop UNDEFINED (skip) or include the value.  None
        # explicitly clears (only meaningful for the
        # attached_entity_* columns).
        for field_name in (
            "event_type",
            "attached_entity_type",
            "attached_entity_id",
            "condition",
            "action_type",
            "action_context",
            "enabled",
            "creator_id",
        ):
            value = getattr(rule, field_name)
            if is_undefined(value):
                continue
            set_values[field_name] = self._serialise_value(field_name, value)

        # Always bump updated_at to now() so callers do not have
        # to manage the timestamp themselves.
        if not set_values:
            # Nothing to update; fetch + return current state.
            current = await self._table.fetch_by_id(
                rule.id, select=self._returning,  # type: ignore[arg-type]
            )
            if not current:
                raise ValueError(f"rule not found: {rule.id}")
            return self._from_record(current)

        record = await self._table.update(
            set=set_values,
            where={"id": rule.id},
            returning=self._returning,
        )
        if not record:
            raise ValueError(f"rule not found: {rule.id}")
        # ``update`` may return a single record or a list; normalise.
        if isinstance(record, list):
            if not record:
                raise ValueError(f"rule not found: {rule.id}")
            return self._from_record(record[0])
        return self._from_record(record)


    async def delete_rule(self, rule_id: str) -> None:
        """Delete the rule with ``rule_id``."""
        deleted = await self._table.delete(
            where={"id": rule_id}, returning="id",
        )
        if not deleted:
            raise ValueError(f"rule not found: {rule_id}")


    # ---- list / filter --------------------------------------------------

    async def list_rules(
        self,
        *,
        event_type: Optional[str] = None,
        attached_entity_type: Optional[AttachedEntityType] = None,
        attached_entity_id: Optional[str] = None,
        enabled_only: bool = False,
        creator_id: Optional[str] = None,
    ) -> List[RuleEntity]:
        """Return rules matching the given filter (all optional, AND'd)."""
        where = WhereClause.empty()

        if event_type:
            where = where.add_and(("event_type", event_type))
        if attached_entity_type:
            where = where.add_and(("attached_entity_type", attached_entity_type))
        if attached_entity_id:
            where = where.add_and(("attached_entity_id", attached_entity_id))
        if enabled_only:
            where = where.add_and(("enabled", True))
        if creator_id:
            where = where.add_and(("creator_id", creator_id))

        staged = (
            self._table.builder.select_from(self._table.name)
            .columns(*self._returning.split(", "))
            .where_clause(where)
            .order_by("created_at ASC")
        )
        stmt = staged.build()
        records = await self._table.fetch(stmt.sql, *stmt.params)
        return [self._from_record(r) for r in records or []]


    async def list_rules_for_event(
        self,
        event_type: str,
    ) -> List[RuleEntity]:
        """Return every enabled rule whose ``event_type`` matches."""
        return await self.list_rules(event_type=event_type, enabled_only=True)


    # ---- (de)serialisation ---------------------------------------------

    def _entity_to_insert_dict(self, rule: RuleEntity) -> Dict[str, Any]:
        """Project a :class:`RuleEntity` to the insertable column dict.

        ``id`` and the timestamps are excluded -- the DB fills
        them.  JSONB columns are serialised to JSON strings so
        asyncpg can pass them through transparently.
        """
        fields = (
            "event_type",
            "attached_entity_type",
            "attached_entity_id",
            "condition",
            "action_type",
            "action_context",
            "enabled",
            "creator_id",
        )
        out: Dict[str, Any] = {}
        for f in fields:
            value = getattr(rule, f)
            if is_undefined(value):
                continue
            out[f] = self._serialise_value(f, value)
        return out

    @staticmethod
    def _serialise_value(field_name: str, value: Any) -> Any:
        """JSON-encode JSONB columns; pass everything else through."""
        if field_name in ("condition", "action_context") and value is not None:
            if not isinstance(value, str):
                return json.dumps(dict(value))
        return value

    @staticmethod
    def _from_record(record: Record) -> RuleEntity:
        """Convert a ``rules`` row into a :class:`RuleEntity`.

        JSONB columns come back from asyncpg as a JSON string;
        parse them back into a dict so the entity carries a
        uniform ``Mapping`` shape regardless of dialect.
        """
        data = dict(record)
        for col in ("condition", "action_context"):
            value = data.get(col)
            if isinstance(value, str):
                try:
                    data[col] = json.loads(value)
                except (TypeError, ValueError):
                    data[col] = {}
        return RuleEntity(**data)


__all__ = ["PostgresRuleRepo"]
