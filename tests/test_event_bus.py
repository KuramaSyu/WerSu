"""Tests for the typed event bus and :class:`Listener` generic.

Covers the contract the rest of the rules subsystem depends on:

* a subclass that parametrises ``Listener[NoteCreated]`` exposes
  ``cls.event is NoteCreated``;
* subscribing a listener to a bus and notifying with a matching
  event triggers it exactly once;
* notifying with a non-matching event does not trigger the
  listener;
* ``unsubscribe`` is idempotent;
* per-listener failures do not abort sibling listeners;
* the :class:`NoopEventBus` accepts subscribe / unsubscribe /
  notify without ever invoking the listener.
"""

from __future__ import annotations

import pytest

from src.api.events.event_bus import EventBus
from src.api.events.events import (
    DirectoryCreated,
    DirectoryUpdated,
    Event,
    NoteCreated,
    NoteUpdated,
)
from src.api.events.listener import Listener, extract_event_type
from src.services.event_bus import InMemoryEventBus, NoopEventBus


# ---- Listener contract ----------------------------------------------------


def test_listener_class_carries_event_classvar():
    """``Listener[NoteCreated]`` exposes ``cls.event is NoteCreated``."""

    class NoteCreatedListener(Listener[NoteCreated]):
        async def on_event(self, event, ctx):
            return None

    assert NoteCreatedListener.event is NoteCreated


def test_extract_event_type_returns_declared_event():
    class L(Listener[NoteUpdated]):
        async def on_event(self, event, ctx):
            return None

    instance = L()
    assert extract_event_type(instance) is NoteUpdated


def test_extract_event_type_raises_on_unresolved_typevar_subclass():
    """A subclass that re-exports a TypeVar but never resolves it errors.

    Mirrors the common pattern::

        E = TypeVar("E", bound=Event)
        class Base(Listener[E], Generic[E]): ...
        class Concrete(Base[NoteCreated]): ...   # resolves
        class Bare(Base): ...                    # does NOT resolve
    """
    from typing import Generic, TypeVar  # noqa: WPS433 -- local import

    E = TypeVar("E", bound=Event)

    class Base(Listener[E], Generic[E]):
        async def on_event(self, event, ctx):
            return None

    class Concrete(Base[NoteCreated]):  # type: ignore[valid-type]
        pass

    # Concrete resolves the typevar, so the event classvar is set.
    assert Concrete.event is NoteCreated

    class Bare(Base):  # type: ignore[valid-type, type-arg]
        pass

    # ``Bare.__orig_bases__`` carries a TypeVar, not a concrete type,
    # so ``event`` is never populated and ``extract_event_type`` raises.
    with pytest.raises(TypeError):
        extract_event_type(Bare())


# ---- Bus dispatch ---------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_bus_routes_to_matching_listener():
    received: list[NoteCreated] = []

    class L(Listener[NoteCreated]):
        async def on_event(self, event, ctx):
            received.append(event)

    bus = InMemoryEventBus()
    listener = L()
    await bus.subscribe(listener)

    event = NoteCreated(note_id="n1", actor_id="u1")
    await bus.notify(event)

    assert received == [event]


@pytest.mark.asyncio
async def test_in_memory_bus_does_not_route_to_other_listeners():
    note_created: list[NoteCreated] = []
    note_updated: list[NoteUpdated] = []

    class A(Listener[NoteCreated]):
        async def on_event(self, event, ctx):
            note_created.append(event)

    class B(Listener[NoteUpdated]):
        async def on_event(self, event, ctx):
            note_updated.append(event)

    bus = InMemoryEventBus()
    await bus.subscribe(A())
    await bus.subscribe(B())

    await bus.notify(NoteCreated(note_id="1", actor_id="u"))
    await bus.notify(NoteUpdated(note_id="2", actor_id="u"))

    assert len(note_created) == 1
    assert note_created[0].note_id == "1"
    assert len(note_updated) == 1
    assert note_updated[0].note_id == "2"


@pytest.mark.asyncio
async def test_in_memory_bus_unsubscribe_is_idempotent():
    seen: list[NoteCreated] = []

    class L(Listener[NoteCreated]):
        async def on_event(self, event, ctx):
            seen.append(event)

    bus = InMemoryEventBus()
    listener = L()
    await bus.subscribe(listener)
    await bus.unsubscribe(listener)
    # second unsubscribe is a no-op, not an error
    await bus.unsubscribe(listener)

    await bus.notify(NoteCreated(note_id="1", actor_id="u"))
    assert seen == []


@pytest.mark.asyncio
async def test_in_memory_bus_unknown_event_is_noop():
    """Notifying with no subscribers does not raise."""

    bus = InMemoryEventBus()
    # No listeners subscribed -- should silently succeed.
    await bus.notify(DirectoryCreated(directory_id="d1", actor_id="u"))


@pytest.mark.asyncio
async def test_in_memory_bus_isolates_per_listener_failures():
    """One failing listener must not abort the others."""

    successes: list[NoteCreated] = []

    class Bad(Listener[NoteCreated]):
        async def on_event(self, event, ctx):
            raise RuntimeError("boom")

    class Good(Listener[NoteCreated]):
        async def on_event(self, event, ctx):
            successes.append(event)

    bus = InMemoryEventBus()
    await bus.subscribe(Bad())
    await bus.subscribe(Good())

    # Must not raise out of notify even though ``Bad`` raised.
    await bus.notify(NoteCreated(note_id="1", actor_id="u"))

    assert len(successes) == 1
    assert successes[0].note_id == "1"


@pytest.mark.asyncio
async def test_in_memory_bus_dispatches_concurrently():
    """Listeners run concurrently, not sequentially.

    Probes the scheduler by using a small sleep; with a serial
    implementation the total time would be 2x the sleep, with the
    concurrent implementation it should be ~1x.
    """
    import asyncio

    started: list[float] = []
    finished: list[float] = []

    class L(Listener[NoteCreated]):
        async def on_event(self, event, ctx):
            started.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.05)
            finished.append(asyncio.get_event_loop().time())

    bus = InMemoryEventBus()
    a = L()
    b = L()
    await bus.subscribe(a)
    await bus.subscribe(b)

    import time
    t0 = time.monotonic()
    await bus.notify(NoteCreated(note_id="1", actor_id="u"))
    elapsed = time.monotonic() - t0

    # Two listeners sleeping 0.05s; serial would be >= 0.1s.
    # Allow generous slack for CI noise.
    assert elapsed < 0.09, f"expected concurrent dispatch, took {elapsed:.3f}s"
    assert len(started) == 2 and len(finished) == 2


# ---- NoopEventBus ---------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_bus_drops_everything():
    seen: list[NoteCreated] = []

    class L(Listener[NoteCreated]):
        async def on_event(self, event, ctx):
            seen.append(event)

    bus: EventBus = NoopEventBus()
    listener = L()
    await bus.subscribe(listener)  # no-op
    await bus.unsubscribe(listener)  # no-op
    await bus.notify(NoteCreated(note_id="1", actor_id="u"))

    assert seen == []


@pytest.mark.asyncio
async def test_noop_bus_does_not_require_listener_event_classvar():
    """Subscribing an instance whose class never resolved ``event`` is fine
    on a no-op bus because the bus never inspects the type.
    """

    class Bare(Listener):  # type: ignore[type-arg]
        async def on_event(self, event, ctx):
            return None

    bus: EventBus = NoopEventBus()
    # Would raise on InMemoryEventBus; must NOT raise here.
    await bus.subscribe(Bare())


# ---- Type-level discrimination --------------------------------------------


def test_listener_event_isolated_per_subclass():
    """Two subclasses parametrised differently each carry their own event."""

    class A(Listener[NoteCreated]):
        async def on_event(self, event, ctx):
            return None

    class B(Listener[DirectoryUpdated]):
        async def on_event(self, event, ctx):
            return None

    assert A.event is NoteCreated
    assert B.event is DirectoryUpdated
    assert A.event is not B.event
