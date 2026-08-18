"""Service-layer contract for the rules subsystem.

The :class:`RuleServiceABC` is the cross-layer contract the gRPC
adapter (and any future REST adapter) talks to.  Its
responsibilities:

* CRUD for :class:`RuleEntity` rows.
* Permission gating on every write -- entity-attached rules
  require ``manage`` on the attached entity; global rules
  require the caller to be a system-level admin (gated via the
  :class:`PermissionServiceABC` for now).
* Payload validation -- the ``condition`` and
  ``action_context`` JSONB blobs are deserialised here so the
  repo only sees validated shapes.

The service is intentionally thin: it does not run conditions or
execute actions -- that is the dispatcher's job.  It only
guarantees that whatever the gRPC client hands in is a
well-formed, authorised rule.

Implementations:
* :class:`src.services.rule_service.RuleServiceImpl`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.events.conditions import Condition
from src.api.events.actions import Action
from src.api.other.user_context import UserContextABC
from src.db.entities.rule import AttachedEntityType, RuleEntity


class RuleServiceError(RuntimeError):
    """Base class for every error raised by the rule service."""


class RulePermissionError(RuleServiceError, PermissionError):
    """Raised when the caller is not allowed to perform the operation."""


class RuleServiceABC(ABC):
    """Application service for rule CRUD with permission gating.

    The service accepts the caller as a
    :class:`~src.api.other.user_context.UserContextABC` and uses
    the injected :class:`~src.api.repos.permission_repo.PermissionRepoABC`
    to enforce the manage-on-attached-entity policy.

    Implementations:
    * :class:`src.services.rule_service.RuleServiceImpl`
    """

    # ---- single-row CRUD ------------------------------------------------

    @abstractmethod
    async def create_rule(
        self,
        rule: RuleEntity,
        actor: UserContextABC,
    ) -> RuleEntity:
        """Create a new rule.

        Args:
            rule: the rule to insert.  ``condition`` and
                ``action_context`` may be either
                pre-serialised mappings (raw ``{"type": ...}``
                dicts) or already-deserialised
                :class:`Condition` / :class:`Action` instances;
                the service normalises both shapes.
            actor: caller identity.  Used to authorise the
                write and to default the ``creator_id`` field
                when the caller leaves it unset.

        Returns:
            :class:`RuleEntity`: the persisted rule.

        Raises:
            RulePermissionError: the caller cannot manage the
                attached entity (or is not a global admin for a
                global rule).
            ValueError: the rule payload is malformed.
        """
        ...

    @abstractmethod
    async def get_rule(
        self,
        rule_id: str,
        actor: UserContextABC,
    ) -> Optional[RuleEntity]:
        """Return the rule with ``rule_id``, or ``None`` if absent.

        Read access is gated on the same rule the caller would
        need to update the rule.
        """
        ...

    @abstractmethod
    async def update_rule(
        self,
        rule: RuleEntity,
        actor: UserContextABC,
    ) -> RuleEntity:
        """Persist changes to an existing rule.

        Args:
            rule: the rule to update.  ``rule.id`` is required.
            actor: caller identity.

        Returns:
            :class:`RuleEntity`: the post-update rule.

        Raises:
            RulePermissionError: the caller cannot manage the rule.
            ValueError: no such rule, or the payload is malformed.
        """
        ...

    @abstractmethod
    async def delete_rule(
        self,
        rule_id: str,
        actor: UserContextABC,
    ) -> None:
        """Delete a rule.

        Args:
            rule_id: id of the rule to delete.
            actor: caller identity.

        Raises:
            RulePermissionError: the caller cannot manage the rule.
            ValueError: no such rule.
        """
        ...

    # ---- list / filter --------------------------------------------------

    @abstractmethod
    async def list_rules(
        self,
        actor: UserContextABC,
        *,
        event_type: Optional[str] = None,
        attached_entity_type: Optional[AttachedEntityType] = None,
        attached_entity_id: Optional[str] = None,
        enabled_only: bool = False,
        creator_id: Optional[str] = None,
    ) -> List[RuleEntity]:
        """Return rules the caller is allowed to see.

        Read access is gated per row: an entity-attached rule
        is only returned if the caller can manage the attached
        entity; a global rule is only returned if the caller is
        a global admin.

        Args:
            actor: caller identity.
            event_type: optional filter.
            attached_entity_type: optional filter.
            attached_entity_id: optional filter.
            enabled_only: when ``True``, skip paused rules.
            creator_id: optional filter; useful for the
                "rules I created" UI.

        Returns:
            List[RuleEntity]: the rules the caller can see,
            filtered by the supplied criteria.
        """
        ...


__all__ = [
    "RuleServiceABC",
    "RuleServiceError",
    "RulePermissionError",
]
