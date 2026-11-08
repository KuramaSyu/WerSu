"""Unit tests for :class:`CalculatedStatsProcessImpl`."""

from __future__ import annotations

import datetime as _dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.background_process.processes.calculated_stats_process import (
    CalculatedStatsProcessImpl,
)


@pytest.fixture
def stats_service() -> MagicMock:
    svc = MagicMock()
    svc.recalculate = AsyncMock(return_value=[])
    return svc


@pytest.fixture
def process(stats_service: MagicMock) -> CalculatedStatsProcessImpl:
    log = MagicMock()
    log.return_value = MagicMock()
    return CalculatedStatsProcessImpl(
        stats_service=stats_service,
        get_now=lambda: _dt.datetime(2026, 1, 1),
        log=log,
        interval=_dt.timedelta(days=1),
    )


async def test_runs_once_at_boot_then_daily(
    process: CalculatedStatsProcessImpl,
) -> None:
    first = await process.next_wakeup()
    second = await process.next_wakeup()

    assert first == _dt.datetime(2026, 1, 1)
    assert second == _dt.datetime(2026, 1, 2)


async def test_run_delegates_full_recalculation(
    process: CalculatedStatsProcessImpl,
    stats_service: MagicMock,
) -> None:
    await process.run()

    stats_service.recalculate.assert_awaited_once_with()


async def test_run_swallows_service_errors(
    process: CalculatedStatsProcessImpl,
    stats_service: MagicMock,
) -> None:
    stats_service.recalculate.side_effect = RuntimeError("boom")

    await process.run()
