"""Scoped data-fetching helper passed to event listeners.

Listeners receive an :class:`EventContext` alongside the event so
they can lazily fetch the data they need (note content, directory
tree, ...) without the event itself holding a reference to a repo.

The interface is intentionally narrow: each method takes the
*entity id* (not the entity) so callers cannot accidentally mutate
the row the event is about.  The implementation is responsible for
permission checks, caching, and session management.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class EventContext(ABC):
    """Scoped, read-mostly view onto the data a listener may fetch.

    Implementations:
    * :class:`src.services.event_context.InMemoryEventContext`
      (production; will use the existing note / directory repos).
    * :class:`NoopEventContext` (used in tests and when the rules
      subsystem is disabled).
    """

    @abstractmethod
    async def note_content(self, note_id: str) -> Optional[str]:
        """Return the current content of ``note_id``, or ``None`` if unknown.

        Implementations may return the raw, unrendered content.
        Listeners that need a specific representation (e.g.
        stripped of attachments) should call the dedicated helper
        once that exists.
        """
        ...

    @abstractmethod
    async def note_title(self, note_id: str) -> Optional[str]:
        """Return the current title of ``note_id``, or ``None`` if unknown."""
        ...

    @abstractmethod
    async def directory_ancestor_ids(self, directory_id: str) -> list[str]:
        """Return ancestor directory ids of ``directory_id``, nearest first.

        The returned list does **not** include ``directory_id``
        itself.  Used by the dispatcher to resolve "is there a rule
        attached to an ancestor of this event's primary entity?"
        -- and exposed here so listeners can ask the same question
        about non-primary entities if they need to.
        """
        ...

    @abstractmethod
    async def note_parent_directory_id(self, note_id: str) -> Optional[str]:
        """Return the immediate parent directory id of a note.

        Returns:
            the parent directory id, or None if the note is an orphan
        """
        ...


class NoopEventContext(EventContext):
    """Event context that returns ``None`` / empty for every call.

    Used in two places:

    1. Tests that do not care about domain data.
    2. The :class:`~src.services.event_bus.NoopEventBus` runtime --
       a listener never fires, so the context is never used; this
       class is here so the bus signature is uniform.
    """

    async def note_content(self, note_id: str) -> Optional[str]:
        return None

    async def note_title(self, note_id: str) -> Optional[str]:
        return None

    async def directory_ancestor_ids(self, directory_id: str) -> list[str]:
        return []

    async def note_parent_directory_id(self, note_id: str) -> Optional[str]:
        return None


__all__ = ["EventContext", "NoopEventContext"]
