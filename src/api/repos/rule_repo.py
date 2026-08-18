"""Storage contract for the rules subsystem.

The :class:`RuleRepoABC` is the cross-layer contract the rule
service depends on for CRUD; the concrete implementations live
under :mod:`src.db.repos.rule` (Postgres) and
:mod:`tests.stubs` (in-memory fake for unit tests).

The repo deliberately does **not** perform any permission or
business validation.  Authorisation, scope matching, condition
evaluation, and action execution belong in the service / rule
dispatcher.

Implementations:
* :class:`src.db.repos.rule.postgres.PostgresRuleRepo`
* :class:`tests.stubs.in_memory_rule_repo.InMemoryRuleRepo`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.events.conditions import Condition
from src.api.events.actions import Action
from src.db.entities.rule import AttachedEntityType, RuleEntity


class RuleRepoABC(ABC):
    """Storage contract for the ``rules`` table.

    The repo round-trips a :class:`RuleEntity` through the row
    shape defined by ``rules.sql``.  ``condition`` is stored as
    JSONB; ``action_type`` (TEXT) + ``action_context`` (JSONB) is
    the split form.

    The repo accepts either pre-parsed :class:`Condition` /
    :class:`Action` dataclasses via the typed helpers, or the raw
    :class:`RuleEntity` for the all-in-one path used by the
    gRPC adapter.
    """

    # ---- single-row CRUD ------------------------------------------------

    @abstractmethod
    async def create_rule(self, rule: RuleEntity) -> RuleEntity:
        """Insert a new rule and return the persisted entity.

        Args:
            rule: rule to insert.  ``condition`` and ``action_context``
                must be set (callers usually pass already-serialised
                JSONB-ready mappings; the repo does not re-serialise).
                ``enabled`` and ``creator_id`` must be concrete.

        Returns:
            :class:`RuleEntity`: the inserted rule, with the
            server-assigned ``id`` and ``created_at`` / ``updated_at``.

        Raises:
            ValueError: when a required field is missing.
        """
        ...

    @abstractmethod
    async def get_rule_by_id(self, rule_id: str) -> Optional[RuleEntity]:
        """Return the rule with ``rule_id``, or ``None`` if absent."""
        ...

    @abstractmethod
    async def update_rule(self, rule: RuleEntity) -> RuleEntity:
        """Persist changes to an existing rule.

        ``rule.id`` is required.  Any field set to
        :obj:`~src.api.undefined.UNDEFINED` is left alone; any
        field set to ``None`` explicitly clears the column
        (only meaningful for the ``attached_entity_*`` columns).
        The ``created_at`` column is never overwritten via this
        path; ``updated_at`` is bumped by the repo.

        Returns:
            :class:`RuleEntity`: the post-update entity.

        Raises:
            ValueError: when ``rule.id`` is missing or no row
                exists for the given id.
        """
        ...

    @abstractmethod
    async def delete_rule(self, rule_id: str) -> None:
        """Delete the rule with ``rule_id``.

        Raises:
            ValueError: when no rule with the given id exists.
        """
        ...

    # ---- list / filter --------------------------------------------------

    @abstractmethod
    async def list_rules(
        self,
        *,
        event_type: Optional[str] = None,
        attached_entity_type: Optional[AttachedEntityType] = None,
        attached_entity_id: Optional[str] = None,
        enabled_only: bool = False,
        creator_id: Optional[str] = None,
    ) -> List[RuleEntity]:
        """Return rules matching the given filter.

        All filter parameters are optional.  When multiple are
        supplied they are AND'd together.  ``enabled_only`` is a
        convenience flag for the dispatcher's hot path; the
        service layer can pass it to skip paused rules without an
        extra ``enabled`` comparison.

        Args:
            event_type: filter to a specific event (e.g. ``"NoteUpdated"``).
            attached_entity_type: filter to rules scoped to a
                specific entity type (``"directory"`` or ``"note"``).
                Pair with ``attached_entity_id`` for an exact match.
            attached_entity_id: filter to rules scoped to a specific
                entity id.
            enabled_only: when ``True``, only return rules with
                ``enabled = true``.
            creator_id: filter to rules created by the given user.

        Returns:
            List[RuleEntity]: matching rules, in insertion order.
        """
        ...

    # ---- typed helpers (for the dispatcher / service) -------------------

    @abstractmethod
    async def list_rules_for_event(
        self,
        event_type: str,
    ) -> List[RuleEntity]:
        """Return every enabled rule whose ``event_type`` matches.

        Used by the rule dispatcher.  Excludes disabled rules so
        the dispatcher can avoid the "evaluate condition" round
        trip for paused rules.

        Args:
            event_type: the event kind, e.g. ``"NoteUpdated"``.

        Returns:
            List[RuleEntity]: matching rules.
        """
        ...


__all__ = ["RuleRepoABC"]
