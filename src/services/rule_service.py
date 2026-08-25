"""Postgres-backed implementation of :class:`RuleServiceABC`.

The service threads the gRPC caller's identity through every
permission check via the
:class:`~src.domain.permission_chain.PermissionCheckChain`
machinery -- no bespoke permission logic in this file.

Permission policy (rules are entity-attached only; global rules
were removed when shelves landed):

* ``attached_entity_type == "directory"`` -- the caller must
  hold ``directory#edit_permissions`` (admin or owner).
* ``attached_entity_type == "note"`` -- the caller must hold
  ``note#manage`` (owner, admin, or admin-on-parent-directory).
* ``attached_entity_type == "shelf"`` -- the caller must hold
  ``shelf#edit_permissions`` (admin or owner).  Shelves share
  the directory role set.

Payload validation runs here, not in the repo, so the repo
stays a thin wrapper.  The service re-uses the
``serialise_condition`` / ``deserialise_condition`` /
``deserialise_action`` helpers from
:mod:`src.api.events.conditions` and
:mod:`src.api.events.actions` to validate the JSONB shapes
before persisting.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

from src.api.events.actions import deserialise_action
from src.api.events.conditions import (
    Condition,
    deserialise_condition,
    serialise_condition,
)
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.other.user_context import UserContextABC
from src.api.repos.rule_repo import RuleRepoABC
from src.api.services.rule_service import (
    RulePermissionError,
    RuleServiceABC,
    RuleServiceError,
)
from src.db.entities.rule import AttachedEntityType, RuleEntity
from src.domain.permission_chain import (
    HasDirectoryEditPermissionsPerm,
    HasNoteManagePerm,
    HasShelfEditPermissionsPerm,
    PermissionCheckChainStart,
)
from src.utils import logging_provider as default_logging_provider


#: The set of event types the rules subsystem knows about.  Kept
#: here (not in :mod:`src.api.events.events`) because the
#: service is the layer that validates user-supplied event_type
#: strings before storing them.
SUPPORTED_EVENT_TYPES: frozenset[str] = frozenset({
    "NoteCreated",
    "NoteUpdated",
    "DirectoryCreated",
    "DirectoryUpdated",
})


class RuleServiceImpl(RuleServiceABC):
    """Postgres-backed rule service with chain-based permission checks.

    Args:
        rule_repo: storage layer.
        permission_repo: passed to every
            :class:`~src.domain.permission_chain.PermissionCheckChain`
            via ``set_permission_repo``.
        directory_facade: required for the "any directory the
            user can manage" check that gates global rule
            creation.  Carries the ``list_user_directory_ids``
            and ``get_children_of`` helpers the chain probes.
        logging_provider: optional logger factory.
    """

    def __init__(
        self,
        rule_repo: RuleRepoABC,
        permission_repo: Any,
        directory_facade: DirectoryFacadeABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._rule_repo = rule_repo
        self._permission_repo = permission_repo
        self._directory_facade = directory_facade
        self.log = (logging_provider or default_logging_provider)(__name__, self)


    # ---- single-row CRUD ------------------------------------------------

    async def create_rule(
        self,
        rule: RuleEntity,
        actor: UserContextABC,
    ) -> RuleEntity:
        """Create a new rule.

        Validates the event type, the condition shape and the
        action shape, then enforces the manage-on-attached-entity
        (or global-admin) permission gate.
        """
        self._validate_event_type(rule.event_type)
        condition = self._validate_condition(rule.condition)
        action_type, action_context = self._validate_action(
            rule.action_type, rule.action_context,
        )
        await self._enforce_create_permission(rule, actor)

        # Default the creator_id to the caller when not supplied.
        creator_id = rule.creator_id
        if is_undefined(creator_id) or creator_id is None:
            creator_id = actor.user_id

        persisted = RuleEntity(
            id=UNDEFINED,
            event_type=rule.event_type,
            attached_entity_type=rule.attached_entity_type,
            attached_entity_id=rule.attached_entity_id,
            condition=dict(serialise_condition(condition)),
            action_type=action_type,
            action_context=action_context,
            enabled=rule.enabled if not is_undefined(rule.enabled) else True,
            creator_id=creator_id,
        )
        return await self._rule_repo.create_rule(persisted)


    async def get_rule(
        self,
        rule_id: str,
        actor: UserContextABC,
    ) -> Optional[RuleEntity]:
        """Return the rule with ``rule_id`` if the caller can manage it."""
        rule = await self._rule_repo.get_rule_by_id(rule_id)
        if rule is None:
            return None
        await self._enforce_write_permission(rule, actor)
        return rule


    async def update_rule(
        self,
        rule: RuleEntity,
        actor: UserContextABC,
    ) -> RuleEntity:
        """Persist changes after re-validating fields and permission."""
        if is_undefined(rule.id) or rule.id is None:
            raise ValueError("rule.id is required for update")

        existing = await self._rule_repo.get_rule_by_id(rule.id)
        if existing is None:
            raise ValueError(f"rule not found: {rule.id}")

        await self._enforce_write_permission(existing, actor)

        # Validate the fields the caller is actually changing.
        if not is_undefined(rule.event_type):
            self._validate_event_type(rule.event_type)
        if not is_undefined(rule.condition):
            self._validate_condition(rule.condition)
        if (
            not is_undefined(rule.action_type)
            or not is_undefined(rule.action_context)
        ):
            # When only one of the two is being updated, fall back
            # to the existing value for the other.
            new_action_type = (
                rule.action_type
                if not is_undefined(rule.action_type)
                else existing.action_type
            )
            new_action_context = (
                rule.action_context
                if not is_undefined(rule.action_context)
                else existing.action_context
            )
            self._validate_action(new_action_type, new_action_context)

        return await self._rule_repo.update_rule(rule)


    async def delete_rule(
        self,
        rule_id: str,
        actor: UserContextABC,
    ) -> None:
        """Delete a rule after the manage-permission check."""
        existing = await self._rule_repo.get_rule_by_id(rule_id)
        if existing is None:
            raise ValueError(f"rule not found: {rule_id}")
        await self._enforce_write_permission(existing, actor)
        await self._rule_repo.delete_rule(rule_id)


    # ---- list / filter --------------------------------------------------

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
        """Return rules the caller can see.

        The repo's :meth:`list_rules` returns the candidate set;
        the service then drops every rule the caller is not
        allowed to see.  Global rules are only returned to global
        admins.
        """
        candidates = await self._rule_repo.list_rules(
            event_type=event_type,
            attached_entity_type=attached_entity_type,
            attached_entity_id=attached_entity_id,
            enabled_only=enabled_only,
            creator_id=creator_id,
        )

        visible: List[RuleEntity] = []
        for rule in candidates:
            try:
                await self._enforce_write_permission(rule, actor)
            except RulePermissionError:
                continue
            visible.append(rule)
        return visible


    # ---- payload validation --------------------------------------------

    @staticmethod
    def _validate_event_type(event_type: Any) -> None:
        """Reject unknown event types; also normalises to ``str``."""
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type must be a non-empty string")
        if event_type not in SUPPORTED_EVENT_TYPES:
            raise ValueError(
                f"unknown event_type {event_type!r}; "
                f"supported: {sorted(SUPPORTED_EVENT_TYPES)}"
            )

    @staticmethod
    def _validate_condition(payload: Any) -> Condition:
        """Parse + validate the condition JSONB shape.

        Accepts an already-deserialised :class:`Condition`
        dataclass or a raw mapping.
        """
        from src.api.events.conditions import AlwaysTrue

        if isinstance(payload, AlwaysTrue):
            return payload  # type: ignore[return-value]
        if not isinstance(payload, Mapping):
            raise ValueError(
                "condition must be a mapping or a Condition dataclass; "
                f"got {type(payload).__name__}"
            )
        return deserialise_condition(payload)

    @staticmethod
    def _validate_action(
        action_type: Any,
        action_context: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Parse + validate the action split, returning ``(type, ctx)``."""
        if isinstance(action_type, str) and isinstance(action_context, Mapping):
            deserialise_action(action_type, action_context)
            return action_type, dict(action_context)
        raise ValueError(
            "action must be a (str action_type, Mapping action_context) pair; "
            f"got ({type(action_type).__name__}, {type(action_context).__name__})"
        )


    # ---- permission enforcement (chain-based) -------------------------

    async def _enforce_create_permission(
        self,
        rule: RuleEntity,
        actor: UserContextABC,
    ) -> None:
        """Gate create on the attached entity (only -- no global rules)."""
        entity_type, entity_id = _require_attached(rule)
        chain = self._attached_entity_chain(entity_type, entity_id)
        result = await chain.check(actor)
        if not result:
            raise RulePermissionError(str(result.error))

    async def _enforce_write_permission(
        self,
        rule: RuleEntity,
        actor: UserContextABC,
    ) -> None:
        """Read / update / delete all share the same gate as create."""
        entity_type, entity_id = _require_attached(rule)
        chain = self._attached_entity_chain(entity_type, entity_id)
        result = await chain.check(actor)
        if not result:
            raise RulePermissionError(str(result.error))

    def _attached_entity_chain(
        self,
        entity_type: AttachedEntityType,
        entity_id: str,
    ) -> Any:
        """Build the single-element chain for an attached-entity rule.

        * ``directory`` -> ``HasDirectoryEditPermissionsPerm``
        * ``note``      -> ``HasNoteManagePerm``
        * ``shelf``     -> ``HasShelfEditPermissionsPerm``
        """
        if entity_type == "directory":
            head: Any = HasDirectoryEditPermissionsPerm(entity_id)
        elif entity_type == "note":
            head = HasNoteManagePerm(entity_id)
        elif entity_type == "shelf":
            head = HasShelfEditPermissionsPerm(entity_id)
        else:
            # ``_require_attached`` already filtered unknown
            # entity types, but keep the type checker honest.
            raise ValueError(
                f"unsupported attached entity type: {entity_type!r}"
            )
        return PermissionCheckChainStart(self._permission_repo).set_next(head)


# ---- module-level helpers -------------------------------------------------


def _is_attached(rule: RuleEntity) -> bool:
    """Return ``True`` when the rule has an attached entity anchor."""
    return (
        not is_undefined(rule.attached_entity_type)
        and rule.attached_entity_type is not None
        and not is_undefined(rule.attached_entity_id)
        and rule.attached_entity_id is not None
    )


def _require_attached(
    rule: RuleEntity,
) -> tuple[AttachedEntityType, str]:
    """Return ``(attached_entity_type, attached_entity_id)`` or raise.

    Global rules (one or both attached fields unset) are no
    longer supported -- the migration that introduces shelves
    deleted every pre-existing global rule, and the rule
    service rejects any new payload that doesn't carry both
    fields.
    """
    if not _is_attached(rule):
        raise ValueError(
            "rule.attached_entity_type and rule.attached_entity_id "
            "are required (global rules are no longer supported)"
        )
    return (
        rule.attached_entity_type,  # type: ignore[return-value]
        rule.attached_entity_id,    # type: ignore[return-value]
    )


__all__ = ["RuleServiceImpl", "SUPPORTED_EVENT_TYPES"]
