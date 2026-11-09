"""Tests for :class:`DailyVersionCompactionProcessImpl`."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.entities.note.versioning import NoteVersionCompactionResult
from src.services.background_process.processes.daily_version_compaction_process import (
    DailyVersionCompactionProcessImpl,
)
from tests.doubles import silent_logger


def _make_process(
    version_repo: MagicMock,
    now: datetime,
    *,
    run_at_hour: int = 0,
) -> DailyVersionCompactionProcessImpl:
    return DailyVersionCompactionProcessImpl(
        version_repo=version_repo,
        get_now=lambda: now,
        log=silent_logger,
        run_at_hour=run_at_hour,
    )


async def test_runs_once_at_boot_then_after_midnight() -> None:
    process = _make_process(MagicMock(), datetime(2026, 5, 18, 20, 0))

    first = await process.next_wakeup()
    second = await process.next_wakeup()

    assert first == datetime(2026, 5, 18, 20, 0)
    assert second == datetime(2026, 5, 19, 0, 5)


async def test_compacts_everything_before_today() -> None:
    repo = MagicMock()
    repo.compact_deltas_before = AsyncMock(
        return_value=NoteVersionCompactionResult(
            notes_compacted=2, deltas_removed=5
        )
    )
    process = _make_process(repo, datetime(2026, 5, 18, 20, 0))

    await process.run()

    repo.compact_deltas_before.assert_awaited_once_with(
        datetime(2026, 5, 18, 0, 0)
    )


async def test_swallows_repo_errors_so_the_scheduler_keeps_running() -> None:
    repo = MagicMock()
    repo.compact_deltas_before = AsyncMock(side_effect=RuntimeError("boom"))
    process = _make_process(repo, datetime(2026, 5, 18, 20, 0))

    await process.run()

    repo.compact_deltas_before.assert_awaited_once()
