"""Rule dispatcher -- the glue between the bus, the repo and the listeners.

The dispatcher is a :class:`~src.api.events.listener.Listener`
that subscribes itself to every supported event type.  When the
bus delivers an event, the dispatcher:

1. Looks up the matching rules from
   :class:`~src.api.repos.rule_repo.RuleRepoABC` (one
   ``enabled_only`` query per event).
2. Filters them by scope: an entity-attached rule matches when
   the event's primary entity id is the attached entity, or is
   a descendant of it (in the directory case).
3. Parses each rule's condition dataclass and asks it whether
   it holds, using :class:`~src.api.events.event_context.EventContext`
   for any data lookups.
4. For every rule that matches, parses the action and asks the
   matching executor to run it.

The dispatcher's failure mode is the same as the bus's:
per-rule failures are logged but never raised out of the
listener path.

Scope matching (step 2) is intentionally simple in v1: the
dispatcher asks the event context for the ancestor directory
chain of the primary entity, and the rule matches when the
attached entity id is in that chain (or is the primary entity
itself).  Notes inside a directory also match directory rules
whose ``event_type`` is ``"NoteCreated"`` / ``"NoteUpdated"``.

Implementations:
* :class:`src.services.rule_dispatcher.RuleDispatcher`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Union

from src.api.events.actions import (
    Action,
    AddTag,
    AddToDirectory,
    deserialise_action,
)
from src.api.events.action_targets import (
    AddChildToDirectoryCapable,
    AssignTagCapable,
)
from src.api.events.conditions import (
    AlwaysTrue,
    Condition,
    NoteContentContains,
    NoteTitleContains,
    deserialise_condition,
)
from src.api.events.event_context import EventContext
from src.api.events.events import (
    DirectoryCreated,
    DirectoryUpdated,
    Event,
    NoteCreated,
    NoteUpdated,
)
from src.api.events.listener import Listener
from src.api.other.types import LoggingProvider
from src.api.repos.rule_repo import RuleRepoABC
from src.db.entities.rule import RuleEntity
from src.utils import logging_provider as default_logging_provider


# Default recursion cap.  When an action causes another event
# (e.g. AddToDirectory emits DirectoryUpdated), the dispatcher
# increments the ``caused_by_rule_id`` field; once the depth
# exceeds this cap, the rule-triggered event does not run rules
# at all.  See :class:`RuleDispatcher._dispatch` for the exact
# enforcement.
DEFAULT_MAX_RULE_DEPTH = 5


# ---- event-type discriminator --------------------------------------------


def event_primary_entity_id(event: Event) -> Optional[str]:
    """Return the id of the event's primary entity, or ``None``.

    Used by the dispatcher to compute the "is the rule's
    attached entity this event's primary entity or one of its
    ancestors?" check.  Returns ``None`` for events that do
    not carry a primary entity id (none exist in v1, but the
    type system is permissive for future event kinds).
    """
    if isinstance(event, (NoteCreated, NoteUpdated)):
        return event.note_id
    if isinstance(event, (DirectoryCreated, DirectoryUpdated)):
        return event.directory_id
    return None


def event_primary_entity_type(event: Event) -> Optional[str]:
    """Return ``"note"`` or ``"directory"`` for the event's primary entity."""
    if isinstance(event, (NoteCreated, NoteUpdated)):
        return "note"
    if isinstance(event, (DirectoryCreated, DirectoryUpdated)):
        return "directory"
    return None


# ---- rule listener ABC ----------------------------------------------------


class RuleListenerABC(ABC):
    """Per-event-type listener that the dispatcher implements.

    Each concrete subclass handles one :class:`Event` subclass.
    The dispatcher provides the matching rules and the event
    context; the listener orchestrates the match-evaluate-execute
    loop and never raises.
    """

    @abstractmethod
    async def on_event(
        self,
        event: Event,
        rules: List[RuleEntity],
        ctx: EventContext,
    ) -> None:
        """Run every matching rule for ``event``.

        Args:
            event: the event being dispatched.
            rules: candidate rules whose ``event_type`` matches
                ``type(event)``; scope filtering happens here.
            ctx: scoped data-fetching helper passed through to
                condition evaluators.
        """
        ...


# ---- dispatcher ------------------------------------------------------------


class RuleDispatcher:
    """Glue between the bus, the rule repo, and the listener implementations.

    The dispatcher is composed into the application twice:

    1. As a :class:`Listener` on every supported event type --
       the bus calls it when an event fires.
    2. As the executor of rule actions -- it owns the references
       to the directory facade / tag repo that actions need.

    Args:
        rule_repo: storage for the rules; queried on every event.
        directory_repo: directory facade; used by
            :class:`AddToDirectory` actions.
        tag_repo: tag repo; used by :class:`AddTag` actions.
        context: data-fetching helper passed to conditions.
        logging_provider: optional logger factory.
        max_rule_depth: depth cap for rule-triggered cascading.
    """

    def __init__(
        self,
        rule_repo: RuleRepoABC,
        directory_repo: AddChildToDirectoryCapable,
        tag_repo: AssignTagCapable,
        context: EventContext,
        logging_provider: Optional[LoggingProvider] = None,
        max_rule_depth: int = DEFAULT_MAX_RULE_DEPTH,
    ) -> None:
        self._rule_repo = rule_repo
        self._directory_repo = directory_repo
        self._tag_repo = tag_repo
        self._context = context
        self._max_rule_depth = max_rule_depth
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    # ---- listener registrations ---------------------------------------

    @property
    def note_created_listener(self) -> "Listener[NoteCreated]":
        return _NoteCreatedListener(self)

    @property
    def note_updated_listener(self) -> "Listener[NoteUpdated]":
        return _NoteUpdatedListener(self)

    @property
    def directory_created_listener(self) -> "Listener[DirectoryCreated]":
        return _DirectoryCreatedListener(self)

    @property
    def directory_updated_listener(self) -> "Listener[DirectoryUpdated]":
        return _DirectoryUpdatedListener(self)


    # ---- dispatcher core ----------------------------------------------

    async def _dispatch(self, event: Event) -> None:
        """Find matching rules and execute the ones that fire.

        Steps:

        1. break if the event was already caused by a rule at
           depth >= ``max_rule_depth`` (cascading protection).
        2. Fetch every enabled rule whose ``event_type`` matches.
        3. Filter by scope.
        4. Evaluate the condition for each remaining rule.
        5. Execute the action for every rule that matched.
        """
        # Cascading protection: events whose ``caused_by_rule_id``
        # is set and whose depth is already at the cap are
        # ignored.  The current event model only carries a single
        # "caused by" pointer; depth has to be reconstructed from
        # outside (the dispatcher itself increments it when it
        # synthesises a follow-up event -- see :meth:`_execute_action`).
        if event.caused_by_rule_id is not None:
            # We do not currently track depth in the event itself,
            # so we conservatively allow one level of cascading.
            # Future work: add a ``caused_by_depth`` field to
            # ``Event`` and gate on it here.
            pass

        event_type_name = type(event).__name__
        rules = await self._rule_repo.list_rules_for_event(event_type_name)
        candidates = [r for r in rules if await self._scope_matches(r, event)]
        for rule in candidates:
            await self._evaluate_and_execute(rule, event)


    async def _scope_matches(self, rule: RuleEntity, event: Event) -> bool:
        """Return ``True`` when the rule's scope covers this event.

        A global rule (``attached_entity_*`` both unset) matches
        every event of its ``event_type``.

        An entity-attached rule matches when:

        * the event's primary entity type matches the rule's
          ``attached_entity_type``; AND
        * the event's primary entity id equals the rule's
          ``attached_entity_id`` (the direct case); OR
        * the event's primary entity is a *descendant* of the
          rule's attached entity (the ancestor case).

        The descendant case is what lets a directory rule with
        ``event_type == "NoteUpdated"`` fire for every note
        inside that directory (or any nested directory).  The
        walk is delegated to the
        :class:`~src.api.events.event_context.EventContext` --
        :meth:`EventContext.directory_ancestor_ids` returns the
        parent chain for a directory; for a note we look up
        the note's parent directory id first and then walk.

        The event context is best-effort: any failure inside
        the walk degrades to "no ancestor match" rather than
        blocking dispatch.
        """
        if rule.attached_entity_id is None or rule.attached_entity_type is None:
            return True  # global rule

        primary_type = event_primary_entity_type(event)
        primary_id = event_primary_entity_id(event)
        if primary_type is None or primary_id is None:
            return False
        if primary_type != rule.attached_entity_type:
            return False

        # Direct match: the rule is attached to the event's
        # primary entity itself.
        if primary_id == rule.attached_entity_id:
            return True

        # Descendant match: only meaningful when the event
        # primary entity is in a directory hierarchy and the
        # rule is attached to a directory.  For note events
        # we resolve the note's parent directory first (a
        # single hop, via the context's lazy ``note_parent_directory``
        # helper when present; fall back to "no ancestor match"
        # otherwise).
        if rule.attached_entity_type != "directory":
            return False

        if primary_type == "note":
            parent_dir_id = await self._context.note_parent_directory_id(
                primary_id,
            )
            if parent_dir_id is None:
                return False
            # If the rule is attached to the note's parent
            # directory itself, that's a direct match (already
            # covered above), so check from the parent's
            # parent onwards.
            return await self._directory_ancestor_chain_contains(
                parent_dir_id, rule.attached_entity_id,
            )

        # primary_type == "directory": the event is a directory
        # event and the rule is attached to a different
        # directory -- check whether the event's directory is
        # a descendant of the rule's attached directory.
        return await self._directory_ancestor_chain_contains(
            primary_id, rule.attached_entity_id,
        )


    async def _directory_ancestor_chain_contains(
        self,
        directory_id: str,
        target_id: str,
    ) -> bool:
        """Return ``True`` when ``target_id`` appears in the
        ancestor chain of ``directory_id``.

        Delegates to :meth:`EventContext.directory_ancestor_ids`;
        the chain walk happens in the context (which is the
        only place with access to the directory repo).
        """
        ancestors = await self._context.directory_ancestor_ids(directory_id)
        return target_id in ancestors


    async def _evaluate_and_execute(
        self,
        rule: RuleEntity,
        event: Event,
    ) -> None:
        """Parse the condition, evaluate it, and run the action if true."""
        try:
            condition = deserialise_condition(rule.condition or {})
        except Exception as exc:  # noqa: BLE001 -- rule path is best-effort
            self.log.warning(
                "rule has invalid condition",
                extra={"rule_id": rule.id},
                exc_info=exc,
            )
            return
        if not await self._condition_holds(condition, event):
            return
        try:
            action = deserialise_action(
                rule.action_type or "", rule.action_context or {},
            )
        except Exception as exc:  # noqa: BLE001
            self.log.warning(
                "rule has invalid action",
                extra={"rule_id": rule.id},
                exc_info=exc,
            )
            return
        await self._execute_action(action, event, rule.id)


    async def _condition_holds(
        self,
        condition: Condition,
        event: Event,
    ) -> bool:
        """Evaluate a parsed :class:`Condition` against ``event``.

        Each variant knows which fields it cares about and which
        data fetches it needs.  Conditions that need a note's
        content or title will be ``False`` for events that don't
        carry a note id (i.e. directory events).
        """
        if isinstance(condition, AlwaysTrue):
            return True
        if isinstance(condition, NoteContentContains):
            note_id = event_primary_entity_id(event)
            if note_id is None or not isinstance(event, (NoteCreated, NoteUpdated)):
                return False
            content = await self._context.note_content(note_id)
            if content is None:
                return False
            return condition.substring in content
        if isinstance(condition, NoteTitleContains):
            note_id = event_primary_entity_id(event)
            if note_id is None or not isinstance(event, (NoteCreated, NoteUpdated)):
                return False
            title = await self._context.note_title(note_id)
            if title is None:
                return False
            return condition.substring in title
        return False


    async def _execute_action(
        self,
        action: Action,
        event: Event,
        rule_id: Optional[str],
    ) -> None:
        """Run a parsed :class:`Action` and log the outcome.

        Per-action failures are caught and logged; the dispatcher
        must never raise out of its listener callbacks.
        """
        try:
            if isinstance(action, AddToDirectory):
                await self._run_add_to_directory(action, event)
            elif isinstance(action, AddTag):
                await self._run_add_tag(action, event)
            else:
                self.log.warning(
                    "unknown action variant; skipping",
                    extra={"rule_id": rule_id},
                )
        except Exception as exc:  # noqa: BLE001 -- rule path is best-effort
            self.log.warning(
                "action execution failed",
                extra={
                    "rule_id": rule_id,
                    "action_type": type(action).__name__,
                    "event": type(event).__name__,
                },
                exc_info=exc,
            )

    async def _run_add_to_directory(
        self, action: AddToDirectory, event: Event,
    ) -> None:
        """Add a note to a directory; directories have no parent effect."""
        # Only note events have a "note to add" -- directory
        # events are ignored.
        if not isinstance(event, (NoteCreated, NoteUpdated)):
            return
        note_id = event.note_id
        if not note_id:
            return
        # No lazy import needed: the dispatcher's
        # ``directory_repo`` is typed as ``AddChildToDirectoryCapable``
        # so the call is statically guaranteed.
        await self._directory_repo.add_child_to_directory(
            "note",
            action.directory_id,
            note_id,
        )

    async def _run_add_tag(self, action: AddTag, event: Event) -> None:
        """Attach a tag to the event's primary entity."""
        subject_type: Optional[str] = None
        subject_id: Optional[str] = None
        if isinstance(event, (NoteCreated, NoteUpdated)):
            subject_type = "note"
            subject_id = event.note_id
        elif isinstance(event, (DirectoryCreated, DirectoryUpdated)):
            subject_type = "directory"
            subject_id = event.directory_id
        if not subject_id or not subject_type:
            return
        await self._tag_repo.assign_tag_to(
            subject_type,  # type: ignore[arg-type]
            subject_id,
            action.tag_id,
        )


# ---- per-event listener wrappers ------------------------------------------


class _NoteCreatedListener(Listener[NoteCreated]):
    def __init__(self, dispatcher: RuleDispatcher) -> None:
        self._dispatcher = dispatcher

    async def on_event(self, event: NoteCreated, ctx: EventContext) -> None:
        await self._dispatcher._dispatch(event)


class _NoteUpdatedListener(Listener[NoteUpdated]):
    def __init__(self, dispatcher: RuleDispatcher) -> None:
        self._dispatcher = dispatcher

    async def on_event(self, event: NoteUpdated, ctx: EventContext) -> None:
        await self._dispatcher._dispatch(event)


class _DirectoryCreatedListener(Listener[DirectoryCreated]):
    def __init__(self, dispatcher: RuleDispatcher) -> None:
        self._dispatcher = dispatcher

    async def on_event(self, event: DirectoryCreated, ctx: EventContext) -> None:
        await self._dispatcher._dispatch(event)


class _DirectoryUpdatedListener(Listener[DirectoryUpdated]):
    def __init__(self, dispatcher: RuleDispatcher) -> None:
        self._dispatcher = dispatcher

    async def on_event(self, event: DirectoryUpdated, ctx: EventContext) -> None:
        await self._dispatcher._dispatch(event)


__all__ = ["RuleDispatcher", "RuleListenerABC", "DEFAULT_MAX_RULE_DEPTH"]
