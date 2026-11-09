"""Daily compaction of old note-version deltas.

Every note save appends a delta row (or a full snapshot every N
deltas).  A busy note can therefore accumulate hundreds of rows, and
the version list becomes noisy.  This process collapses the deltas of
each *completed* calendar day into a single delta per note/snapshot,
keeping only that day's final state restorable.

The compaction is deliberately limited to days before ``today``:
today's deltas are still being written, and the newest version must
stay exactly as the user saved it.  Because a group that already has a
single delta is a no-op, running the process again is idempotent and
also catches up on days where the service was down.

Snapshots are never deleted here.  A snapshot created mid-day starts a
new group, so compaction cannot merge deltas that belong to different
snapshot bases.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Callable, Optional

from src.api.other.types import LoggingProvider
from src.api.services.background_process.background_process import (
    BackgroundProcessABC,
)
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.db.repos.note.versioning import NoteVersionRepoABC


class DailyVersionCompactionProcessImpl(BackgroundProcessABC):
    """Collapse every completed day's deltas into one delta per note.

    Args:
        version_repo: versioning repository that owns the snapshot and
            delta tables.
        get_now: clock used for scheduling and for computing the
            "before today" cutoff; injected so tests can pin time.
        log: logging-provider factory.
        run_at_hour: hour of the day (local/naive time) at which the
            daily compaction should start after the boot catch-up.
    """

    def __init__(
        self,
        version_repo: NoteVersionRepoABC,
        get_now: Callable[[], datetime],
        log: LoggingProvider,
        *,
        run_at_hour: int = 0,
    ) -> None:
        self._version_repo = version_repo
        self._get_now = get_now
        self.log = log(__name__, self)
        self._run_at_hour = run_at_hour
        self._handle: Optional[SchedulerHandleABC] = None
        self._first_wakeup = True

    def attach_handle(self, handle: SchedulerHandleABC) -> None:
        self._handle = handle

    async def next_wakeup(self) -> Optional[datetime]:
        """Run immediately at boot, then once a day after midnight."""
        now = self._get_now()
        if self._first_wakeup:
            self._first_wakeup = False
            return now

        next_day = now.date() + timedelta(days=1)
        return datetime.combine(next_day, time(hour=self._run_at_hour, minute=5))

    async def run(self) -> None:
        """Compact all deltas created before today's midnight."""
        now = self._get_now()
        cutoff = datetime.combine(now.date(), time.min)
        try:
            result = await self._version_repo.compact_deltas_before(cutoff)
        except Exception as exc:  # noqa: BLE001
            self.log.exception(
                f"DailyVersionCompactionProcess: compaction failed: {exc!r}"
            )
            return

        if result.deltas_removed == 0:
            self.log.debug(
                "DailyVersionCompactionProcess: nothing to compact before "
                f"{cutoff.isoformat()}"
            )
            return

        self.log.info(
            "DailyVersionCompactionProcess: compacted "
            f"{result.notes_compacted} note(s), removed "
            f"{result.deltas_removed} delta(s) before {cutoff.isoformat()}"
        )


__all__ = ["DailyVersionCompactionProcessImpl"]
