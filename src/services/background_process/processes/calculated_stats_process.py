"""Daily recalculation of the ``calculated_stats`` table.

The process runs once at boot (catch-up) and then once per
``interval`` (default: one day).  ``run`` delegates to
:meth:`CalculatedStatsServiceABC.recalculate` with no filter, so every
user is refreshed.

Wiring a note-update trigger that wakes this process for a single user
is intentionally left to a follow-up task.  The service already
supports it via
``recalculate(CalculatedStatsFilter(user_ids=[...]))``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from src.api.other.types import LoggingProvider
from src.api.services.background_process.background_process import (
    BackgroundProcessABC,
)
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.api.services.calculated_stats_service import CalculatedStatsServiceABC


class CalculatedStatsProcessImpl(BackgroundProcessABC):
    """Refresh every user's precomputed stats once per day.

    Args:
        stats_service: service that owns the recalculation logic.
        get_now: clock used for scheduling; injected so tests can pin
            time instead of sleeping.
        log: logging-provider factory.
        interval: delay between full sweeps.  Defaults to one day.
    """

    def __init__(
        self,
        stats_service: CalculatedStatsServiceABC,
        get_now: Callable[[], datetime],
        log: LoggingProvider,
        *,
        interval: timedelta = timedelta(days=1),
    ) -> None:
        self._stats_service = stats_service
        self._get_now = get_now
        self.log = log(__name__, self)
        self._interval = interval
        self._handle: Optional[SchedulerHandleABC] = None
        self._first_wakeup = True

    def attach_handle(self, handle: SchedulerHandleABC) -> None:
        self._handle = handle

    async def next_wakeup(self) -> Optional[datetime]:
        """Run immediately after boot, then once per ``interval``."""
        now = self._get_now()
        if self._first_wakeup:
            self._first_wakeup = False
            return now
        return now + self._interval

    async def run(self) -> None:
        """Recalculate stats for every user."""
        self.log.info("CalculatedStatsProcess: starting full recalculation")
        try:
            rows = await self._stats_service.recalculate()
        except Exception as exc:  # noqa: BLE001
            self.log.exception(
                f"CalculatedStatsProcess: recalculation failed: {exc!r}"
            )
            return
        self.log.info(
            f"CalculatedStatsProcess: recalculated {len(rows)} user(s)"
        )


__all__ = ["CalculatedStatsProcessImpl"]
