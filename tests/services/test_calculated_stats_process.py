"""Unit tests for :class:`CalculatedStatsProcessImpl`."""

from __future__ import annotations

import datetime as _dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.events.events import NoteCreated, NoteUpdated
from src.db.entities.calculated_stats import CalculatedStatsFilter
from src.services.background_process.processes.calculated_stats_process import (
    CalculatedStatsNoteCreatedListener,
    CalculatedStatsNoteUpdatedListener,
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


async def test_notify_user_changed_queues_and_wakes(
    process: CalculatedStatsProcessImpl,
    stats_service: MagicMock,
) -> None:
    handle = MagicMock()
    handle.wake_at = AsyncMock()
    process.attach_handle(handle)

    await process.notify_user_changed("u1")

    handle.wake_at.assert_awaited_once()
    _, why = handle.wake_at.await_args.args
    assert why["user_id"] == "u1"

    await process.run()

    stats_service.recalculate.assert_awaited_once_with(
        CalculatedStatsFilter(user_ids=["u1"])
    )


async def test_run_recalculates_only_dirty_users_and_dedupes(
    process: CalculatedStatsProcessImpl,
    stats_service: MagicMock,
) -> None:
    await process.notify_user_changed("u1")
    await process.notify_user_changed("u2")
    await process.notify_user_changed("u1")

    await process.run()

    stats_service.recalculate.assert_awaited_once_with(
        CalculatedStatsFilter(user_ids=["u1", "u2"])
    )

    # The dirty set is consumed: the next run is a full sweep again.
    stats_service.recalculate.reset_mock()
    await process.run()
    stats_service.recalculate.assert_awaited_once_with()


async def test_notify_without_handle_still_recalculates_on_run(
    process: CalculatedStatsProcessImpl,
    stats_service: MagicMock,
) -> None:
    await process.notify_user_changed("u1")

    await process.run()

    stats_service.recalculate.assert_awaited_once_with(
        CalculatedStatsFilter(user_ids=["u1"])
    )


async def test_notify_ignores_empty_user_id(
    process: CalculatedStatsProcessImpl,
    stats_service: MagicMock,
) -> None:
    await process.notify_user_changed("")

    await process.run()

    stats_service.recalculate.assert_awaited_once_with()


async def test_note_listener_forwards_actor_to_process() -> None:
    process = MagicMock()
    process.notify_user_changed = AsyncMock()
    listener = CalculatedStatsNoteCreatedListener(process)

    await listener.on_event(NoteCreated(note_id="n1", actor_id="u1"), MagicMock())

    process.notify_user_changed.assert_awaited_once_with("u1")


async def test_note_updated_listener_forwards_actor_to_process() -> None:
    process = MagicMock()
    process.notify_user_changed = AsyncMock()
    listener = CalculatedStatsNoteUpdatedListener(process)

    await listener.on_event(NoteUpdated(note_id="n1", actor_id="u2"), MagicMock())

    process.notify_user_changed.assert_awaited_once_with("u2")
