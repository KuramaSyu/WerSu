"""Abstract bus for delivering domain events to listeners.

The bus is the seam between "something happened" (the activity
logger, the note service, ...) and "react to it" (rules, future
metrics aggregators, future webhook bridges, ...).  Producers
**notify**; consumers **subscribe**.  No producer knows which
listeners exist.

Three implementations are expected:

* :class:`src.services.event_bus.NoopEventBus` -- drop every event.
  Use when the rules subsystem is disabled.
* :class:`src.services.event_bus.InMemoryEventBus` -- fan out
  immediately, run listeners concurrently via ``asyncio.gather``.
  Used in tests and in dev when no background worker is wired.
* A background / queue-backed bus (deferred) -- enqueues each
  event onto the existing :mod:`src.services.background_process`
  pipeline so listeners run in a worker, off the request thread.

The bus does **not** know about rule matching, conditions, or
actions.  That logic lives in the rule dispatcher (deferred);
the bus only knows about listeners.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.api.events.events import Event
from src.api.events.listener import Listener


class EventBus(ABC):
    """Subscribe / unsubscribe / notify contract.

    Implementations:
    * :class:`src.services.event_bus.NoopEventBus`
    * :class:`src.services.event_bus.InMemoryEventBus`
    """

    @abstractmethod
    async def subscribe(self, listener: Listener) -> None:
        """Register ``listener`` to receive events of its declared type.

        Args:
            listener: an object whose class inherits from
                :class:`~src.api.events.listener.Listener` and
                parametrises it with a concrete
                :class:`~src.api.events.events.Event` subclass.

        Raises:
            TypeError: when ``listener`` does not declare a concrete
                ``Listener[Event]`` parameter.
        """
        ...

    @abstractmethod
    async def unsubscribe(self, listener: Listener) -> None:
        """Remove ``listener`` so it stops receiving events.

        Idempotent: removing a listener that was never subscribed
        (or was already removed) is a no-op, not an error.
        """
        ...

    @abstractmethod
    async def notify(self, event: Event) -> None:
        """Deliver ``event`` to every listener subscribed for its type.

        Implementations decide whether to dispatch synchronously
        (in-memory bus) or enqueue to a worker (background bus).
        The activity logger awaits this call so the bus must
        return promptly; long-running work should happen inside
        the listener, not the bus.
        """
        ...


__all__ = ["EventBus"]
