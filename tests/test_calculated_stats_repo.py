"""Unit tests for :class:`PostgresCalculatedStatsRepo`.

The repo is exercised with :class:`RecordingTable`, so the tests assert
the row shape and the aggregate plumbing without a real Postgres.
"""

from __future__ import annotations

import datetime as _dt

from src.db.entities.calculated_stats import CalculatedStatsEntity
from src.db.repos.calculated_stats.postgres import PostgresCalculatedStatsRepo
from tests.doubles import RecordingTable


def _repo(
    *,
    stats_table: RecordingTable | None = None,
    note_table: RecordingTable | None = None,
    directory_table: RecordingTable | None = None,
    subdirectory_table: RecordingTable | None = None,
    user_table: RecordingTable | None = None,
) -> PostgresCalculatedStatsRepo:
    return PostgresCalculatedStatsRepo(
        stats_table=stats_table or RecordingTable(name="calculated_stats"),
        note_content_table=note_table or RecordingTable(name="note.content"),
        directory_table=directory_table or RecordingTable(name="note.directory"),
        directory_subdirectory_table=(
            subdirectory_table
            or RecordingTable(name="note.directory_subdirectory")
        ),
        user_table=user_table or RecordingTable(name="auth.user"),
    )


async def test_upsert_shapes_row() -> None:
    stats_table = RecordingTable(
        name="calculated_stats", id_fields=["user_id"]
    )
    repo = _repo(stats_table=stats_table)
    calculated_at = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)

    result = await repo.upsert(
        CalculatedStatsEntity(
            user_id="u1",
            total_chars=10,
            total_notes=2,
            total_chapters=3,
            total_books=4,
            calculated_at=calculated_at,
        )
    )

    assert result.user_id == "u1"
    call = stats_table.calls_of("upsert")[0]
    assert call.kwargs["where"] == {
        "user_id": "u1",
        "total_chars": 10,
        "total_notes": 2,
        "total_chapters": 3,
        "total_books": 4,
        "calculated_at": calculated_at,
    }


async def test_get_reads_stats_row() -> None:
    stats_table = RecordingTable(
        name="calculated_stats",
        id_fields=["user_id"],
        seed={
            "u1": {
                "user_id": "u1",
                "total_chars": 7,
                "total_notes": 1,
                "total_chapters": 0,
                "total_books": 1,
                "calculated_at": None,
            }
        },
    )
    repo = _repo(stats_table=stats_table)

    result = await repo.get("u1")

    assert result is not None
    assert result.total_chars == 7
    assert result.total_books == 1


async def test_aggregate_notes_empty_short_circuits() -> None:
    note_table = RecordingTable(name="note.content")
    repo = _repo(note_table=note_table)

    result = await repo.aggregate_notes([])

    assert result.total_notes == 0
    assert result.total_chars == 0
    assert note_table.calls_of("fetch") == []


async def test_aggregate_notes_maps_sql_result() -> None:
    note_table = RecordingTable(
        name="note.content",
        fetch_returns=[{"total_notes": 3, "total_chars": 42}],
    )
    repo = _repo(note_table=note_table)

    result = await repo.aggregate_notes(["n1", "n2", "n3"])

    assert result.total_notes == 3
    assert result.total_chars == 42
    sql = note_table.calls_of("fetch")[0].kwargs["sql"]
    assert "FROM note.content" in sql
    assert "LENGTH(content)" in sql


async def test_aggregate_directories_maps_sql_result() -> None:
    directory_table = RecordingTable(
        name="note.directory",
        fetch_returns=[{"total_books": 2, "total_chapters": 5}],
    )
    subdirectory_table = RecordingTable(
        name="note.directory_subdirectory"
    )
    repo = _repo(
        directory_table=directory_table,
        subdirectory_table=subdirectory_table,
    )

    result = await repo.aggregate_directories(["d1", "d2"])

    assert result.total_books == 2
    assert result.total_chapters == 5
    sql = directory_table.calls_of("fetch")[0].kwargs["sql"]
    assert "FROM note.directory d" in sql
    assert "note.directory_subdirectory" in sql


async def test_list_user_ids_reads_auth_user() -> None:
    user_table = RecordingTable(
        name="auth.user",
        seed={
            "u1": {"id": "u1"},
            "u2": {"id": "u2"},
        },
    )
    repo = _repo(user_table=user_table)

    assert await repo.list_user_ids() == ["u1", "u2"]
    sql = user_table.calls_of("fetch")[0].kwargs["sql"]
    # Only human users get a stats row; system / temporary are skipped.
    assert "type = 'human'" in sql
