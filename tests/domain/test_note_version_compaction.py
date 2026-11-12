"""Unit tests for the pure :class:`DailyVersionCompactor`."""

from __future__ import annotations

from datetime import datetime

from src.domain.note_version_compaction import (
    DailyVersionCompactor,
    DeltaVersion,
    SnapshotVersion,
)
from src.domain.note_version_patch import build_patch

NOTE = "note-1"
DAY_ONE = datetime(2026, 5, 18, 9, 0, 0)
DAY_TWO = datetime(2026, 5, 19, 9, 0, 0)
CUTOFF = datetime(2026, 5, 20, 0, 0, 0)


def _snapshot(
    index: int,
    content: str,
    *,
    created_at: datetime = DAY_ONE,
    title: str = "t",
) -> SnapshotVersion:
    return SnapshotVersion(
        snapshot_id=f"snap-{index}",
        note_id=NOTE,
        version_index=index,
        created_at=created_at,
        author_id="user-1",
        title=title,
        content=content,
    )


def _delta(
    index: int,
    snapshot_id: str,
    old: str,
    new: str,
    *,
    created_at: datetime = DAY_ONE,
    old_title: str = "t",
    new_title: str = "t",
) -> DeltaVersion:
    return DeltaVersion(
        delta_id=f"delta-{index}",
        note_id=NOTE,
        snapshot_id=snapshot_id,
        version_index=index,
        created_at=created_at,
        author_id="user-1",
        title_patch=build_patch(old_title, new_title),
        content_patch=build_patch(old, new),
    )


def test_plan_collapses_deltas_into_existing_snapshot() -> None:
    snapshots = [_snapshot(1, "a\n")]
    deltas = [
        _delta(2, "snap-1", "a\n", "b\n"),
        _delta(3, "snap-1", "b\n", "c\n"),
    ]

    plan = DailyVersionCompactor().plan(snapshots, deltas, CUTOFF)

    assert len(plan.days) == 1
    day = plan.days[0]
    assert day.note_id == NOTE
    assert day.retained_snapshot.snapshot_id == "snap-1"
    assert day.retained_snapshot.version_index == 3
    assert day.retained_snapshot.content == "c\n"
    assert day.delta_ids == ("delta-2", "delta-3")
    assert day.snapshot_ids_to_delete == ()
    assert plan.notes_compacted == 1
    assert plan.deltas_removed == 2
    assert plan.snapshots_removed == 0


def test_plan_collapses_many_snapshots() -> None:
    snapshots = [_snapshot(index, f"content-{index}\n") for index in range(1, 31)]

    plan = DailyVersionCompactor().plan(snapshots, [], CUTOFF)

    assert len(plan.days) == 1
    day = plan.days[0]
    assert day.retained_snapshot.snapshot_id == "snap-30"
    assert day.retained_snapshot.version_index == 30
    assert day.retained_snapshot.content == "content-30\n"
    assert day.delta_ids == ()
    assert len(day.snapshot_ids_to_delete) == 29
    assert plan.snapshots_removed == 29


def test_plan_collapses_snapshots_and_deltas() -> None:
    contents = {index: f"v{index}\n" for index in range(1, 35)}
    snapshots = [_snapshot(index, contents[index]) for index in (1, 12, 23)]
    deltas = []
    for start, snap_id in ((2, "snap-1"), (13, "snap-12"), (24, "snap-23")):
        for index in range(start, start + 10):
            deltas.append(
                _delta(index, snap_id, contents[index - 1], contents[index])
            )

    plan = DailyVersionCompactor().plan(snapshots, deltas, CUTOFF)

    assert len(plan.days) == 1
    day = plan.days[0]
    assert day.retained_snapshot.snapshot_id == "snap-23"
    assert day.retained_snapshot.version_index == 33
    assert day.retained_snapshot.content == "v33\n"
    assert len(day.delta_ids) == 30
    assert day.snapshot_ids_to_delete == ("snap-1", "snap-12")
    assert plan.deltas_removed == 30
    assert plan.snapshots_removed == 2


def test_plan_creates_snapshot_for_delta_only_day() -> None:
    snapshots = [_snapshot(1, "one\n", created_at=DAY_ONE)]
    deltas = [
        _delta(2, "snap-1", "one\n", "two\n", created_at=DAY_TWO),
        _delta(3, "snap-1", "two\n", "three\n", created_at=DAY_TWO),
    ]

    plan = DailyVersionCompactor().plan(snapshots, deltas, CUTOFF)

    assert len(plan.days) == 1
    day = plan.days[0]
    assert day.day == DAY_TWO.date()
    assert day.retained_snapshot.is_new
    assert day.retained_snapshot.snapshot_id is None
    assert day.retained_snapshot.version_index == 3
    assert day.retained_snapshot.content == "three\n"
    assert day.delta_ids == ("delta-2", "delta-3")
    assert plan.deltas_removed == 2


def test_plan_ignores_today_and_single_version_days() -> None:
    snapshots = [
        _snapshot(1, "a\n", created_at=DAY_ONE),
        _snapshot(2, "b\n", created_at=DAY_TWO),
    ]
    deltas = [
        _delta(3, "snap-2", "b\n", "c\n", created_at=DAY_TWO),
    ]

    plan = DailyVersionCompactor().plan(snapshots, deltas, CUTOFF)

    # Day one has a single version; day two has two versions and is
    # compacted.  Nothing from today (>= cutoff) is touched.
    assert [day.day for day in plan.days] == [DAY_TWO.date()]


def test_plan_is_idempotent() -> None:
    snapshots = [_snapshot(1, "a\n")]
    deltas = [
        _delta(2, "snap-1", "a\n", "b\n"),
        _delta(3, "snap-1", "b\n", "c\n"),
    ]
    compactor = DailyVersionCompactor()

    first = compactor.plan(snapshots, deltas, CUTOFF)
    assert len(first.days) == 1

    # Apply the plan in memory: one retained snapshot, no deltas.
    retained = first.days[0].retained_snapshot
    compacted_snapshots = [
        SnapshotVersion(
            snapshot_id=retained.snapshot_id or "new",
            note_id=retained.note_id,
            version_index=retained.version_index,
            created_at=retained.created_at,
            author_id=retained.author_id,
            title=retained.title,
            content=retained.content,
        )
    ]
    second = compactor.plan(compacted_snapshots, [], CUTOFF)

    assert second.days == ()
    assert second.deltas_removed == 0
    assert second.snapshots_removed == 0


def test_plan_reconstructs_title_and_content() -> None:
    snapshots = [_snapshot(1, "body\n", title="old")]
    deltas = [
        _delta(
            2,
            "snap-1",
            "body\n",
            "body\nmore\n",
            old_title="old",
            new_title="new",
        )
    ]

    plan = DailyVersionCompactor().plan(snapshots, deltas, CUTOFF)

    retained = plan.days[0].retained_snapshot
    assert retained.title == "new"
    assert retained.content == "body\nmore\n"
