"""Shared base for the user-action background processes.

Both :class:`UserDisableProcessImpl` and :class:`UserEnableProcessImpl`
walk the pending :class:`~src.db.entities.user.user_action.UserActionEntity`
rows of their kind, mark every one whose ``execute_at`` has passed
as executed, and ask the scheduler when to come back.  The only
thing that differs is the ``action`` literal the filter targets and
the ``action_name`` used in the `why` payload.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Optional

from src.api.other.types import LoggingProvider
from src.api.repos.user_action_repo import UserActionRepoABC
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.api.services.background_process.user_action_process import UserActionProcessABC
from src.db.entities.user.user_action import FilterUserAction, UserActionEntity


class _UserActionByKindProcessImpl(UserActionProcessABC):
    """Execute pending actions whose ``action`` matches the subclass's literal."""

    kind: str
    action_name: str

    def __init__(
        self,
        user_action_repo: UserActionRepoABC,
        get_now: Callable[[], datetime],
        log: LoggingProvider,
    ) -> None:
        self._repo = user_action_repo
        self._get_now = get_now
        self.log = log(__name__, self)
        self._handle: Optional[SchedulerHandleABC] = None

    def attach_handle(self, handle: SchedulerHandleABC) -> None:
        self._handle = handle

    async def next_wakeup(self) -> Optional[datetime]:
        pending = await self._fetch_due_pending()
        if not pending:
            return None
        return min(pending, key=lambda a: a.execute_at).execute_at  # type: ignore[return-value]

    async def run(self) -> None:
        due = await self._fetch_due_pending()
        if not due:
            self.log.debug(f"{self.action_name}: no due actions")
            return
        user_ids = [str(a.user_id) for a in due]
        self.log.info(
            f"{self.action_name}: executing {len(due)} action(s) "
            f"for user_id(s)={','.join(user_ids)}"
        )
        now = self._normalise(self._get_now())
        for action in due:
            action.executed_at = now
            await self._repo.update_action(action)
            self.log.info(
                f"{self.action_name}: user_id={action.user_id} "
                f"action_id={action.id} scheduled_at={action.execute_at}"
            )
        self.log.info(
            f"{self.action_name}: completed {len(due)} action(s) "
            f"for user_id(s)={','.join(user_ids)}"
        )

    async def _fetch_due_pending(self) -> List[UserActionEntity]:
        """Every pending action of our kind whose ``execute_at`` has passed."""
        flt = FilterUserAction(action=self.kind, executed_at=None)
        pending = await self._repo.get_actions(flt)
        now = self._normalise(self._get_now())
        return [
            a for a in pending
            if a.execute_at
            and self._normalise(a.execute_at) <= now  # type: ignore[arg-type,return-value]
        ]

    @staticmethod
    def _normalise(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt