"""Unit tests for PostgresPreferencesRepo.

Drives the repo against a fake DatabaseABC so the SQL generation
can be verified without booting Postgres. Also exercises the
entity-shaped read / upsert paths that delegate to the
PostgresRowConverter visitor.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from tests.doubles import _RecordingDatabase
from src.api.services.preferences_service import PreferenceScope
from src.db.repos.preferences.postgres import PostgresPreferencesRepo
from src.grpc_mod.converter.postgres_row_converter import PostgresRowConverter


_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_repo(
    db: _RecordingDatabase,
    *,
    now: Optional[Any] = None,
) -> PostgresPreferencesRepo:
    """Build a PostgresPreferencesRepo with a shared row converter.

    Each test gets its own converter instance so injected ``now``
    values stay scoped to the test.
    """
    converter = PostgresRowConverter(now=now)
    return PostgresPreferencesRepo(
        db=db,
        to_postgres_row=converter,
        now=now,
    )


# ---------------------------------------------------------------------------
# scope -> SQL column
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "expected_column"),
    [
        ("user", "user_id"),
        ("shelf", "shelf_id"),
        ("role", "role_id"),
    ],
)
def test_upsert_targets_correct_owner_column(
    scope: PreferenceScope, expected_column: str
) -> None:
    """Each scope must address its own owner column in INSERT and DELETE."""
    db = _RecordingDatabase(fetchrow_responses=[None])
    repo = _make_repo(db)
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    import asyncio

    asyncio.run(
        repo.upsert(scope, "the-id", {"k": "v"}, updated_at=ts)
    )
    db.execute_response = "DELETE 1"
    asyncio.run(repo.delete(scope, "the-id"))

    insert_sql, insert_args = db.execute_calls[0]
    delete_sql, delete_args = db.execute_calls[1]

    assert f"INSERT INTO preferences ({expected_column}" in insert_sql
    assert (
        f"ON CONFLICT ({expected_column}) "
        f"WHERE {expected_column} IS NOT NULL" in insert_sql
    )
    assert insert_args == ("the-id", json.dumps({"k": "v"}), ts)

    assert delete_sql == f"DELETE FROM preferences WHERE {expected_column} = $1"
    assert delete_args == ("the-id",)


def test_unknown_scope_raises_clear_error() -> None:
    """An unrecognised scope must raise a ValueError; no SQL is run."""
    db = _RecordingDatabase(fetchrow_responses=[None])
    repo = _make_repo(db)

    import asyncio

    with pytest.raises(ValueError, match="unsupported preference scope"):
        asyncio.run(repo.read("bogus", "x"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unsupported preference scope"):
        asyncio.run(repo.upsert("bogus", "x", {}))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unsupported preference scope"):
        asyncio.run(repo.delete("bogus", "x"))  # type: ignore[arg-type]

    assert db.fetchrow_calls == []
    assert db.execute_calls == []


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def test_read_returns_empty_dict_when_no_row() -> None:
    """A missing row resolves to {}; no exception propagates."""
    import asyncio

    db = _RecordingDatabase(fetchrow_responses=[None])
    repo = _make_repo(db)

    result = asyncio.run(repo.read("user", "u1"))

    assert result == {}
    sql, args = db.fetchrow_calls[-1]
    assert sql == "SELECT payload FROM preferences WHERE user_id = $1"
    assert args == ("u1",)


def test_read_decodes_json_string_payload() -> None:
    """A text payload from a non-postgres dialect still parses as JSON."""
    import asyncio

    db = _RecordingDatabase(
        fetchrow_responses=[{"payload": json.dumps({"theme": "dark"})}]
    )
    repo = _make_repo(db)

    result = asyncio.run(repo.read("user", "u1"))

    assert result == {"theme": "dark"}


def test_read_returns_empty_dict_when_payload_is_blank() -> None:
    """Null and empty-string payloads must both resolve to {}."""
    import asyncio

    db = _RecordingDatabase(fetchrow_responses=[{"payload": None}])
    repo = _make_repo(db)
    assert asyncio.run(repo.read("user", "u1")) == {}

    db = _RecordingDatabase(fetchrow_responses=[{"payload": ""}])
    repo = _make_repo(db)
    assert asyncio.run(repo.read("user", "u1")) == {}


def test_read_returns_empty_dict_on_invalid_json() -> None:
    """Bad JSON falls back to {} instead of raising into the service."""
    import asyncio

    db = _RecordingDatabase(fetchrow_responses=[{"payload": "{not json"}])
    repo = _make_repo(db)
    assert asyncio.run(repo.read("user", "u1")) == {}


def test_read_passes_through_dict_payload() -> None:
    """If the record already carries a dict (e.g. asyncpg codec) skip JSON."""
    import asyncio

    db = _RecordingDatabase(fetchrow_responses=[{"payload": {"theme": "light"}}])
    repo = _make_repo(db)
    assert asyncio.run(repo.read("shelf", "shelf-7")) == {"theme": "light"}


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


def test_upsert_uses_injected_timestamp() -> None:
    """updated_at injection lets tests pin the column value."""
    import asyncio

    db = _RecordingDatabase(fetchrow_responses=[None])
    repo = _make_repo(db)
    pinned = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

    asyncio.run(repo.upsert("role", "r1", {"x": 1}, updated_at=pinned))

    sql, args = db.execute_calls[-1]
    assert "INSERT INTO preferences (role_id, payload, updated_at)" in sql
    assert "VALUES ($1, $2::jsonb, $3)" in sql
    assert "ON CONFLICT (role_id) WHERE role_id IS NOT NULL" in sql
    assert args == ("r1", json.dumps({"x": 1}), pinned)


def test_upsert_defaults_updated_at_to_now_factory() -> None:
    """Without an explicit timestamp the repo uses the now factory."""
    import asyncio

    db = _RecordingDatabase(fetchrow_responses=[None])
    pinned = datetime(2030, 5, 1, tzinfo=timezone.utc)
    repo = _make_repo(db, now=lambda: pinned)

    asyncio.run(repo.upsert("user", "u1", {"a": 1}))

    _, args = db.execute_calls[-1]
    assert args[0] == "u1"
    assert args[2] == pinned


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("DELETE 1", True),
        ("DELETE 0", False),
        ("DELETE 5", True),
        ("DELETE 0\n", False),
    ],
)
def test_delete_parses_affected_row_count(
    response: str, expected: bool
) -> None:
    """asyncpg returns DELETE <n>; the repo extracts the trailing count."""
    import asyncio

    db = _RecordingDatabase(fetchrow_responses=[None])
    db.execute_response = response
    repo = _make_repo(db)

    result = asyncio.run(repo.delete("user", "u1"))

    assert result is expected


def test_delete_returns_false_when_status_unparseable() -> None:
    """Garbage status strings should be guarded, not crash."""
    import asyncio

    db = _RecordingDatabase(fetchrow_responses=[None])
    db.execute_response = "weird status"
    repo = _make_repo(db)

    assert asyncio.run(repo.delete("user", "u1")) is False


# ---------------------------------------------------------------------------
# read_entity / upsert_entity
# ---------------------------------------------------------------------------


def test_read_entity_returns_none_when_no_row_exists() -> None:
    """Missing row -> None."""
    import asyncio

    from src.api.other.undefined import UNDEFINED

    db = _RecordingDatabase(fetchrow_responses=[None])
    repo = _make_repo(db)

    result = asyncio.run(repo.read_entity("user", "u1"))

    assert result is None
    sql, args = db.fetchrow_calls[-1]
    assert "SELECT id, user_id AS owner_id, payload, updated_at" in sql
    assert args == ("u1",)


def test_read_entity_builds_full_preferences_entity() -> None:
    """read_entity returns a PreferencesEntity shaped from the row."""
    import asyncio

    db = _RecordingDatabase(
        fetchrow_responses=[{
            "id": "row-1",
            "owner_id": "u1",
            "payload": json.dumps({"theme": "dark"}),
            "updated_at": _TS,
        }]
    )
    repo = _make_repo(db)

    result = asyncio.run(repo.read_entity("user", "u1"))

    assert result is not None
    assert result.id == "row-1"
    assert result.user_id == "u1"
    assert result.payload == {"theme": "dark"}
    assert result.updated_at == _TS


def test_upsert_entity_uses_visitor_to_compute_row_dict() -> None:
    """upsert_entity delegates to PostgresRowConverter.visit_preferences."""
    import asyncio

    from src.db.entities.preferences import PreferencesEntity

    db = _RecordingDatabase(fetchrow_responses=[None])
    repo = _make_repo(db, now=lambda: _TS)
    entity = PreferencesEntity(
        scope="shelf",
        shelf_id="s-1",
        payload={"theme": "light"},
        updated_at=_TS,
    )

    asyncio.run(repo.upsert_entity(entity))

    sql, args = db.execute_calls[-1]
    assert "INSERT INTO preferences (shelf_id, payload, updated_at)" in sql
    assert args[0] == "s-1"
    assert json.loads(args[1]) == {"theme": "light"}
    assert args[2] == _TS


def test_upsert_entity_rejects_entity_with_no_owner_for_scope() -> None:
    """A scope/owner mismatch must raise before we hit SQL."""
    import asyncio

    from src.db.entities.preferences import PreferencesEntity

    db = _RecordingDatabase(fetchrow_responses=[None])
    repo = _make_repo(db)
    entity = PreferencesEntity(
        scope="user",
        shelf_id="s-1",
        payload={},
        updated_at=_TS,
    )

    with pytest.raises(ValueError, match="row missing owner value"):
        asyncio.run(repo.upsert_entity(entity))

    assert db.execute_calls == []
