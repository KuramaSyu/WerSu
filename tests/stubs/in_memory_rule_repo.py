"""In-memory :class:`RuleRepoABC` fake for unit tests.

Stores rules in a plain ``dict`` keyed on the row id.  Mirrors
the surface area of :class:`src.db.repos.rule.postgres.PostgresRuleRepo`
without touching a database; the rule service / dispatcher tests
that need a repo can drop this in.

The implementation is intentionally tiny: tests that need realistic
filter / sort behaviour should use the Postgres repo against the
testcontainers Postgres fixture.  This fake is for fast unit
testing of the service / dispatcher / gRPC adapter in isolation.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.repos.rule_repo import RuleRepoABC
from src.db.entities.rule import AttachedEntityType, RuleEntity


class InMemoryRuleRepo(RuleRepoABC):
    """In-memory :class:`RuleRepoABC` for unit tests.

    All persisted rules live in ``self._rules``; ids are generated
    with ``uuid.uuid4()`` to keep them unique even when a test
    creates many rules.  Timestamps are populated with
    ``datetime.now(timezone.utc)`` on insert and bumped on every
    update, mirroring the DB column defaults.
    """

    def __init__(self) -> None:
        self._rules: Dict[str, RuleEntity] = {}


    async def create_rule(self, rule: RuleEntity) -> RuleEntity:
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

        now = datetime.now(timezone.utc)
        rule_id = str(uuid.uuid4())
        persisted = RuleEntity(
            id=rule_id,
            event_type=rule.event_type,
            attached_entity_type=rule.attached_entity_type,
            attached_entity_id=rule.attached_entity_id,
            condition=rule.condition,
            action_type=rule.action_type,
            action_context=rule.action_context,
            enabled=rule.enabled,
            creator_id=rule.creator_id,
            created_at=now,
            updated_at=now,
        )
        self._rules[rule_id] = persisted
        return replace(persisted)  # type: ignore[call-arg]


    async def get_rule_by_id(self, rule_id: str) -> Optional[RuleEntity]:
        rule = self._rules.get(rule_id)
        if rule is None:
            return None
        return replace(rule)  # type: ignore[call-arg]


    async def update_rule(self, rule: RuleEntity) -> RuleEntity:
        if is_undefined(rule.id) or rule.id is None:
            raise ValueError("rule.id is required for update")
        existing = self._rules.get(rule.id)
        if existing is None:
            raise ValueError(f"rule not found: {rule.id}")

        now = datetime.now(timezone.utc)
        updated = RuleEntity(
            id=existing.id,
            event_type=rule.event_type
                if not is_undefined(rule.event_type) else existing.event_type,
            attached_entity_type=(
                rule.attached_entity_type
                if not is_undefined(rule.attached_entity_type)
                else existing.attached_entity_type
            ),
            attached_entity_id=(
                rule.attached_entity_id
                if not is_undefined(rule.attached_entity_id)
                else existing.attached_entity_id
            ),
            condition=rule.condition
                if not is_undefined(rule.condition) else existing.condition,
            action_type=rule.action_type
                if not is_undefined(rule.action_type) else existing.action_type,
            action_context=rule.action_context
                if not is_undefined(rule.action_context)
                else existing.action_context,
            enabled=rule.enabled
                if not is_undefined(rule.enabled) else existing.enabled,
            creator_id=rule.creator_id
                if not is_undefined(rule.creator_id) else existing.creator_id,
            created_at=existing.created_at,
            updated_at=now,
        )
        self._rules[rule.id] = updated
        return replace(updated)  # type: ignore[call-arg]


    async def delete_rule(self, rule_id: str) -> None:
        if rule_id not in self._rules:
            raise ValueError(f"rule not found: {rule_id}")
        del self._rules[rule_id]


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
        out: List[RuleEntity] = []
        for rule in self._rules.values():
            if event_type is not None and rule.event_type != event_type:
                continue
            if (
                attached_entity_type is not None
                and rule.attached_entity_type != attached_entity_type
            ):
                continue
            if (
                attached_entity_id is not None
                and rule.attached_entity_id != attached_entity_id
            ):
                continue
            if enabled_only and not rule.enabled:
                continue
            if creator_id is not None and rule.creator_id != creator_id:
                continue
            out.append(replace(rule))  # type: ignore[call-arg]
        # Stable ordering: created_at ASC, then id ASC for determinism.
        out.sort(key=lambda r: (r.created_at or datetime.min, r.id or ""))
        return out


    async def list_rules_for_event(
        self,
        event_type: str,
    ) -> List[RuleEntity]:
        return await self.list_rules(event_type=event_type, enabled_only=True)
