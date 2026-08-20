"""In-memory and no-op :class:`EventBus` implementations.

The :class:`InMemoryEventBus` fans out each event to every listener
that subscribed for its concrete type, running the listeners
concurrently with :func:`asyncio.gather` and isolating per-listener
failures via ``return_exceptions=True``.  One bad listener must
not abort the rest.

The :class:`NoopEventBus` is the no-op bus used when the rules
subsystem is disabled (or in tests that do not care about
event delivery).  The activity logger wires the bus as a
constructor parameter and defaults to :class:`NoopEventBus` so
existing call sites do not need to be changed.

The :class:`EventContext` used for in-memory delivery is
:class:`~src.api.events.event_context.NoopEventContext`.  A
production context that talks to the note / directory repos
will live in a follow-up; the listener API does not change
when it lands.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Dict, List

from src.api.events.event_bus import EventBus
from src.api.events.event_context import EventContext, NoopEventContext
from src.api.events.events import Event
from src.api.events.listener import Listener, extract_event_type
from src.utils import logging_provider as default_logging_provider


class NoopEventBus(EventBus):
    """Bus that silently drops every event.

    All three operations are no-ops.  Subscribing a listener is
    accepted (and discarded) so call sites can register their
    listeners unconditionally.
    """

    async def subscribe(self, listener: Listener) -> None:
        """Discard ``listener``; nothing to register."""
        return None

    async def unsubscribe(self, listener: Listener) -> None:
        """Discard ``listener``; nothing to remove."""
        return None

    async def notify(self, event: Event) -> None:
        """Discard ``event``; no listener will receive it."""
        return None


class InMemoryEventBus(EventBus):
    """In-process bus that fans events out to subscribed listeners.

    Listeners are stored in a ``{event_type: [Listener, ...]}`` map
    keyed on the concrete event subclass declared by each listener.
    On :meth:`notify` the bus looks up listeners for the event's
    exact runtime type and dispatches them concurrently.

    Per-listener failures are caught and logged; the bus does not
    raise out of :meth:`notify` and one bad listener does not
    prevent the rest from running.  The failure is logged with the
    listener's class name and the event's ``type(event).__name__``
    so the operator can trace what happened.

    Thread-safety: this class is intended for use inside a single
    asyncio event loop.  Concurrent subscribe / unsubscribe from
    other threads is not supported; the underlying ``defaultdict``
    is not synchronised.
    """

    def __init__(self, context: EventContext | None = None) -> None:
        self._listeners: Dict[type, List[Listener]] = defaultdict(list)
        self._context: EventContext = context or NoopEventContext()
        self.log = default_logging_provider(__name__, self)

    async def subscribe(self, listener: Listener) -> None:
        """Register ``listener`` under its declared event type.

        Args:
            listener: any :class:`Listener` subclass instance.  The
                event type is read from ``listener.event``
                (auto-populated from the generic).

        Raises:
            TypeError: when ``listener`` does not declare a concrete
                ``Listener[Event]`` parameter.
        """
        event_type = extract_event_type(listener)
        self._listeners[event_type].append(listener)

    async def unsubscribe(self, listener: Listener) -> None:
        """Remove ``listener`` from the bus.

        Idempotent: removing a listener that was never subscribed
        (or was already removed) is a no-op.

        Raises:
            TypeError: when ``listener`` does not declare a concrete
                ``Listener[Event]`` parameter (the classvar
                extraction is the same code path as
                :meth:`subscribe`).
        """
        event_type = extract_event_type(listener)
        bucket = self._listeners.get(event_type)
        if not bucket:
            return
        try:
            bucket.remove(listener)
        except ValueError:
            # already removed -- nothing to do
            return

    async def notify(self, event: Event) -> None:
        """Deliver ``event`` to every listener subscribed for its type.

        Listeners are dispatched concurrently via
        :func:`asyncio.gather` with ``return_exceptions=True``;
        per-listener failures are logged but never re-raised.

        The current implementation matches on the event's exact
        runtime type only -- base-class dispatch (``Listener[Event]``
        receiving every concrete event) is intentionally not
        supported in v1 because it would silently slow down
        delivery for every subscription.
        """
        event_type = type(event)
        listeners = list(self._listeners.get(event_type, ()))
        if not listeners:
            return
        await asyncio.gather(
            *(self._safe_invoke(listener, event) for listener in listeners),
            return_exceptions=True,
        )

    async def _safe_invoke(self, listener: Listener, event: Event) -> None:
        """Run one listener, catching and logging any exception."""
        try:
            await listener.on_event(event, self._context)
        except Exception as exc:  # noqa: BLE001 -- intentional isolation
            self.log.warning(
                "listener raised during event dispatch",
                extra={
                    "listener": type(listener).__name__,
                    "event": type(event).__name__,
                },
                exc_info=exc,
            )


__all__ = ["InMemoryEventBus", "NoopEventBus"]
