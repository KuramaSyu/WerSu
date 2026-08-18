"""Typed listener base class for the event bus.

The point of :class:`Listener` is to let callers write::

    class NoteCreatedListener(Listener[NoteCreated]):
        async def on_event(self, event: NoteCreated, ctx: EventContext) -> None:
            ...

and have the event type carried by the class itself -- so the bus
can route an emitted :class:`NoteCreated` to every listener whose
``event`` classvar matches, without the caller having to wire up the
mapping by hand.

We use the PEP 695 class-parameter syntax (Python 3.12+; this
project requires 3.14) so the generic ``EventT`` is declared on
the class without a separate ``TypeVar`` symbol.  At class-creation
time :meth:`__init_subclass__` resolves the concrete type
subclassed with (by inspecting ``__orig_bases__`` /
``get_args``) and stores it on :attr:`event` as a classvar.  The
bus uses that classvar as the routing key.

Subclasses parametrise ``Listener[...]`` directly with the
concrete event type -- no need for ``Generic[E]`` in user code
anymore.  When a subclass does not parametrise with a concrete
type (e.g. an intermediate base that re-exports the typevar),
``event`` stays unset and subscribing an instance raises
:exc:`TypeError`.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, ClassVar, get_args, get_origin

if TYPE_CHECKING:
    from src.api.events.event_context import EventContext
    from src.api.events.events import Event


class Listener[EventT: Event]:
    """Base class for any object that wants to receive domain events.

    Type parameters:
        EventT: the concrete :class:`Event` subclass this listener
            reacts to.  Bound to :class:`Event` so the bus can
            identify the routing key as a class type.

    Subclasses must:

    1. parametrise :class:`Listener` with a concrete event subclass,
       e.g. ``class MyListener(Listener[NoteCreated])``;
    2. implement :meth:`on_event`.

    The bus reads :attr:`event` (populated by
    :meth:`__init_subclass__` from the generic parameter) to route
    events.  There is no need to override it.
    """

    #: Concrete event subclass this listener reacts to.  Populated
    #: by :meth:`__init_subclass__` from the generic parameter
    #: passed to ``Listener[...]``; do not set it manually.
    event: ClassVar[type[EventT]]


    def __init_subclass__(cls, **kwargs: object) -> None:
        """Resolve the concrete event type for ``cls`` and store it.

        Walks ``__orig_bases__`` looking for a base whose
        :func:`typing.get_origin` is :class:`Listener` (or a
        subclass of it) and extracts the first concrete
        :class:`Event` subclass from the type arguments.  Bases
        parametrised with a :data:`TypeVar` (i.e. the typevar was
        not yet resolved) are skipped -- the corresponding
        subclass's ``event`` classvar stays unset and subscribing
        an instance will raise at subscription time.
        """
        super().__init_subclass__(**kwargs)
        # Lazy import to avoid a circular dependency: events.py is
        # imported at module load, but we only need ``Event`` to
        # type-check the parameter here, not at class creation.
        from src.api.events.events import Event as _Event

        for base in getattr(cls, "__orig_bases__", ()):
            origin = get_origin(base)
            if not isinstance(origin, type):
                continue
            if not issubclass(origin, Listener):
                continue
            for arg in get_args(base):
                if isinstance(arg, type) and issubclass(arg, _Event):
                    cls.event = arg
                    return


    @abstractmethod
    async def on_event(self, event: EventT, ctx: "EventContext") -> None:
        """Handle one event delivered by the bus.

        Args:
            event: the event being delivered.  The runtime type
                matches :attr:`event`; the static type matches the
                generic bound.
            ctx: scoped helper that exposes the data-fetching API a
                listener is allowed to call (e.g. ``ctx.note_content``).
                Listeners that do not need extra data may ignore it.

        Raises:
            Any exception: the bus catches and logs per-listener
                failures so one bad listener does not abort the
                rest.  See :class:`~src.services.event_bus.InMemoryEventBus`.
        """
        ...


def extract_event_type(listener: Listener) -> type[Event]:
    """Return the concrete event class a listener was declared against.

    Convenience wrapper around the :attr:`Listener.event` classvar
    that raises a descriptive error when the classvar is missing --
    which happens when the listener was declared without a concrete
    ``Listener[...]`` parameter.

    Args:
        listener: any object whose class inherits from
            :class:`Listener`.

    Returns:
        type[Event]: the concrete event subclass this listener
        reacts to.

    Raises:
        TypeError: when the listener's class does not declare a
            concrete event type.
    """
    event_type = getattr(type(listener), "event", None)
    if event_type is None:
        raise TypeError(
            f"{type(listener).__name__} does not declare a concrete "
            f"Listener[Event] parameter; cannot subscribe it to the bus"
        )
    return event_type
