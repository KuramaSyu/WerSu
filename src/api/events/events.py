"""Domain events emitted by the application.

Events are immutable, data-only dataclasses.  They carry just enough
information for a listener to decide whether to act, and any
heavier data (note content, directory tree, ...) is fetched lazily
via :class:`~src.api.events.event_context.EventContext`.

Two design rules:

* **No async methods on the event.**  The event dataclass stays
  data-only so it is trivially constructable in tests, can be
  serialised to JSON, and never holds a reference to a repo.
* **Common base only carries the audit fields.**  Per-kind payload
  lives on the subclass so the bus can dispatch on ``type(event)``
  without inspecting fields.

The list of concrete events grows as the application surfaces
behaviour the rules subsystem should be able to react to.  Currently:

* :class:`NoteCreated`      -- a new note was inserted
* :class:`NoteUpdated`      -- a note was edited (full or partial)
* :class:`DirectoryCreated` -- a new directory was inserted
* :class:`DirectoryUpdated` -- a directory was renamed / re-parented
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Event:
    """Base class for every domain event the bus knows about.

    Subclasses add the per-kind payload.  Listeners branch on
    ``type(event)`` (or, equivalently, on the generic bound to
    :class:`~src.api.events.listener.Listener`).

    Attributes:
        actor_id: id of the user that triggered the event, or
            ``None`` when the event was emitted by a system job.
        caused_by_rule_id: id of the rule that produced this event
            when it was itself triggered by a rule.  The dispatcher
            uses this for depth-limiting; events caused by rules do
            not trigger further rules past the configured depth.
    """

    actor_id: Optional[str] = None
    caused_by_rule_id: Optional[str] = None


@dataclass(frozen=True)
class NoteCreated(Event):
    """A new note was created.

    Carries the note id; content / title are fetched lazily through
    the :class:`~src.api.events.event_context.EventContext` because
    most rules do not need them.
    """

    note_id: str = ""


@dataclass(frozen=True)
class NoteUpdated(Event):
    """A note was edited.

    ``changed_fields`` is an optional hint the activity logger can
    populate (e.g. ``("title", "content")``) so listeners can
    short-circuit on "content didn't change".  When empty, the event
    is treated as a generic update.
    """

    note_id: str = ""
    changed_fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DirectoryCreated(Event):
    """A new directory was created."""

    directory_id: str = ""


@dataclass(frozen=True)
class DirectoryUpdated(Event):
    """A directory was renamed, re-parented, or otherwise mutated."""

    directory_id: str = ""
    changed_fields: Tuple[str, ...] = ()


__all__ = [
    "Event",
    "NoteCreated",
    "NoteUpdated",
    "DirectoryCreated",
    "DirectoryUpdated",
]
