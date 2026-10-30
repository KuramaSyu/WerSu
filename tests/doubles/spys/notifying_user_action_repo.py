from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.db.entities.user.user_action import FilterUserAction, UserActionEntity, UserActionKind
from src.db.repos.user.notifying_user_action_repo import (
    NotifyingUserActionRepo,
    UserActionListener,
)


class _RecordingSchedulerHandle(SchedulerHandleABC):
    """Scheduler handle that records every ``wake_at`` call.

    ``fail_next`` lets a single test force the next ``wake_at`` to
    raise so the decorator's failure isolation path is exercised.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: List[tuple[datetime, Dict[str, Any]]] = []
        self.fail_next: Optional[Exception] = None

    async def wake_at(self, when: datetime, why: Dict[str, Any]) -> None:
        if self.fail_next is not None:
            exc = self.fail_next
            self.fail_next = None
            raise exc
        self.calls.append((when, dict(why)))


class _Listeners:
    """Per-kind :class:`UserActionListener` factory for tests.

    Builds one :class:`UserActionListener` per requested kind, each
    wrapping a :class:`_RecordingSchedulerHandle`.  The decorator is
    given the listener list directly; tests read recorded calls off
    :attr:`handles`.
    """

    def __init__(
        self,
        kinds: tuple[UserActionKind, ...] = ("disable", "enable", "delete"),
    ) -> None:
        self.handles: Dict[UserActionKind, _RecordingSchedulerHandle] = {
            kind: _RecordingSchedulerHandle(name=f"handle-{kind}") for kind in kinds
        }
        self.listeners: List[UserActionListener] = [
            UserActionListener(kind=kind, handle=handle)
            for kind, handle in self.handles.items()
        ]


__all__ = [
    '_RecordingSchedulerHandle',
    '_Listeners',
]
