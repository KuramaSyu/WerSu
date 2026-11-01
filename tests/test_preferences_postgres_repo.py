"""Unit tests for PostgresPreferencesRepo.

Drives the repo against a RecordingTable so the SQL generation and
table calls can be verified without booting Postgres. Also exercises
the entity-shaped read / upsert paths that delegate to the
PostgresRowConverter visitor.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from tests.doubles import RecordingTable
from src.api.repos.preferences_repo import PreferencesLimitExceeded
from src.api.services.preferences_service import PreferenceScope
from src.db.repos.preferences.postgres import PostgresPreferencesRepo
from src.grpc_mod.converter.postgres_row_converter import PostgresRowConverter


_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_table(**kwargs: Any) -> RecordingTable:
    """Build a preferences-shaped RecordingTable."""
    kwargs.setdefault("name", "preferences")
    return RecordingTable(**kwargs)


def _make_repo(
    table: RecordingTable,
    *,
    now: Optional[Any] = None,
    max_preferences: int = 50,
) -> PostgresPreferencesRepo:
    """Build a PostgresPreferencesRepo with a shared row converter.

    Each test gets its own converter instance so injected ``now``
    values stay scoped to the test.
    """
    converter = PostgresRowConverter(now=now)
    return PostgresPreferencesRepo(
        table=table,
        to_postgres_row=converter,
        now=now,
        max_preferences=max_preferences,
    )


def _fetch_sql_args(table: RecordingTable) -> tuple[str, tuple]:
    """Return (collapsed_sql, args) for the last fetch call."""
    call = table.calls_of("fetch")[-1]
    return call.kwargs["sql"], tuple(call.kwargs["args"])


# ---------------------------------------------------------------------------
# scope -> owner column
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
    import asyncio

    table = _make_table(fetch_returns=[{"key": ""}])
    repo = _make_repo(table)
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    asyncio.run(repo.upsert(scope, "the-id", {"k": "v"}, updated_at=ts))
    asyncio.run(repo.delete(scope, "the-id"))

    sql, args = _fetch_sql_args(table)
    assert (
        f"INSERT INTO preferences ({expected_column}, key, payload, updated_at)"
        in sql
    )
    assert (
        f"ON CONFLICT ({expected_column}, key) "
        f"WHERE {expected_column} IS NOT NULL" in sql
    )
    assert args == ("the-id", "", json.dumps({"k": "v"}), ts, 50)

    delete = table.calls_of("delete")[0]
    assert delete.kwargs["where"] == {expected_column: "the-id", "key": ""}


def test_sql_uses_table_name_from_wrapper() -> None:
    """The table name comes from the injected wrapper, not a literal."""
    import asyncio

    table = _make_table(name="custom_preferences", fetch_returns=[{"key": ""}])
    repo = _make_repo(table)

    asyncio.run(repo.upsert("user", "u1", {"a": 1}, updated_at=_TS))

    sql, _ = _fetch_sql_args(table)
    assert "INSERT INTO custom_preferences" in sql
    assert "FROM custom_preferences" in sql


def test_unknown_scope_raises_clear_error() -> None:
    """An unrecognised scope must raise a ValueError; no table call is made."""
    import asyncio

    table = _make_table()
    repo = _make_repo(table)

    with pytest.raises(ValueError, match="unsupported preference scope"):
        asyncio.run(repo.read("bogus", "x"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unsupported preference scope"):
        asyncio.run(repo.upsert("bogus", "x", {}))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unsupported preference scope"):
        asyncio.run(repo.delete("bogus", "x"))  # type: ignore[arg-type]

    assert table.calls == []


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def test_read_returns_empty_dict_when_no_row() -> None:
    """A missing row resolves to {}; no exception propagates."""
    import asyncio

    table = _make_table()
    repo = _make_repo(table)

    result = asyncio.run(repo.read("user", "u1"))

    assert result == {}
    call = table.calls_of("select_row")[-1]
    assert call.kwargs["where"] == {"user_id": "u1", "key": ""}
    assert call.kwargs["select"] == "payload"


def test_read_decodes_json_string_payload() -> None:
    """A text payload from a non-postgres dialect still parses as JSON."""
    import asyncio

    table = _make_table(seed={
        "r1": {
            "id": "r1",
            "user_id": "u1",
            "key": "",
            "payload": json.dumps({"theme": "dark"}),
        }
    })
    repo = _make_repo(table)

    result = asyncio.run(repo.read("user", "u1"))

    assert result == {"theme": "dark"}


def test_read_returns_empty_dict_when_payload_is_blank() -> None:
    """Null and empty-string payloads must both resolve to {}."""
    import asyncio

    for blank in (None, ""):
        table = _make_table(seed={
            "r1": {"id": "r1", "user_id": "u1", "key": "", "payload": blank}
        })
        repo = _make_repo(table)
        assert asyncio.run(repo.read("user", "u1")) == {}


def test_read_returns_empty_dict_on_invalid_json() -> None:
    """Bad JSON falls back to {} instead of raising into the service."""
    import asyncio

    table = _make_table(seed={
        "r1": {"id": "r1", "user_id": "u1", "key": "", "payload": "{not json"}
    })
    repo = _make_repo(table)
    assert asyncio.run(repo.read("user", "u1")) == {}


def test_read_passes_through_dict_payload() -> None:
    """If the record already carries a dict (e.g. asyncpg codec) skip JSON."""
    import asyncio

    table = _make_table(seed={
        "r1": {
            "id": "r1",
            "shelf_id": "shelf-7",
            "key": "",
            "payload": {"theme": "light"},
        }
    })
    repo = _make_repo(table)
    assert asyncio.run(repo.read("shelf", "shelf-7")) == {"theme": "light"}


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


def test_upsert_uses_injected_timestamp() -> None:
    """updated_at injection lets tests pin the column value."""
    import asyncio

    table = _make_table(fetch_returns=[{"key": ""}])
    repo = _make_repo(table)
    pinned = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

    asyncio.run(repo.upsert("role", "r1", {"x": 1}, updated_at=pinned))

    sql, args = _fetch_sql_args(table)
    assert "INSERT INTO preferences (role_id, key, payload, updated_at)" in sql
    assert "SELECT $1, $2, $3::jsonb, $4" in sql
    assert "ON CONFLICT (role_id, key) WHERE role_id IS NOT NULL" in sql
    assert args == ("r1", "", json.dumps({"x": 1}), pinned, 50)


def test_upsert_defaults_updated_at_to_now_factory() -> None:
    """Without an explicit timestamp the repo uses the now factory."""
    import asyncio

    table = _make_table(fetch_returns=[{"key": ""}])
    pinned = datetime(2030, 5, 1, tzinfo=timezone.utc)
    repo = _make_repo(table, now=lambda: pinned)

    asyncio.run(repo.upsert("user", "u1", {"a": 1}))

    _, args = _fetch_sql_args(table)
    assert args[0] == "u1"
    assert args[3] == pinned


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_returns_true_when_row_existed() -> None:
    """A matched row reports True."""
    import asyncio

    table = _make_table(seed={
        "r1": {"id": "r1", "user_id": "u1", "key": ""}
    })
    repo = _make_repo(table)

    assert asyncio.run(repo.delete("user", "u1")) is True


def test_delete_returns_false_when_no_row() -> None:
    """No match reports False."""
    import asyncio

    table = _make_table()
    repo = _make_repo(table)

    assert asyncio.run(repo.delete("user", "u1")) is False


def test_delete_passes_key_through() -> None:
    """delete() scopes to the requested key."""
    import asyncio

    table = _make_table(seed={
        "r1": {"id": "r1", "user_id": "u1", "key": "layout"}
    })
    repo = _make_repo(table)

    assert asyncio.run(repo.delete("user", "u1", "layout")) is True
    assert table.calls_of("delete")[-1].kwargs["where"] == {
        "user_id": "u1",
        "key": "layout",
    }


# ---------------------------------------------------------------------------
# read_entity / upsert_entity
# ---------------------------------------------------------------------------


def test_read_entity_returns_none_when_no_row_exists() -> None:
    """Missing row -> None."""
    import asyncio

    table = _make_table()
    repo = _make_repo(table)

    result = asyncio.run(repo.read_entity("user", "u1"))

    assert result is None
    call = table.calls_of("select_row")[-1]
    assert call.kwargs["where"] == {"user_id": "u1", "key": ""}
    assert call.kwargs["select"] == "*"


def test_read_entity_builds_full_preferences_entity() -> None:
    """read_entity returns a PreferencesEntity shaped from the row."""
    import asyncio

    table = _make_table(seed={
        "r1": {
            "id": "row-1",
            "user_id": "u1",
            "key": "layout",
            "payload": json.dumps({"theme": "dark"}),
            "updated_at": _TS,
        }
    })
    repo = _make_repo(table)

    result = asyncio.run(repo.read_entity("user", "u1", "layout"))

    assert result is not None
    assert result.id == "row-1"
    assert result.user_id == "u1"
    assert result.key == "layout"
    assert result.payload == {"theme": "dark"}
    assert result.updated_at == _TS


def test_upsert_entity_uses_visitor_to_compute_row_dict() -> None:
    """upsert_entity delegates to PostgresRowConverter.visit_preferences."""
    import asyncio

    from src.db.entities.preferences import PreferencesEntity

    table = _make_table(fetch_returns=[{"key": ""}])
    repo = _make_repo(table, now=lambda: _TS)
    entity = PreferencesEntity(
        scope="shelf",
        shelf_id="s-1",
        payload={"theme": "light"},
        updated_at=_TS,
    )

    asyncio.run(repo.upsert_entity(entity))

    sql, args = _fetch_sql_args(table)
    assert "INSERT INTO preferences (shelf_id, key, payload, updated_at)" in sql
    assert args[0] == "s-1"
    assert args[1] == ""
    assert json.loads(args[2]) == {"theme": "light"}
    assert args[3] == _TS


def test_upsert_entity_rejects_entity_with_no_owner_for_scope() -> None:
    """A scope/owner mismatch must raise before we hit the table."""
    import asyncio

    from src.db.entities.preferences import PreferencesEntity

    table = _make_table()
    repo = _make_repo(table)
    entity = PreferencesEntity(
        scope="user",
        shelf_id="s-1",
        payload={},
        updated_at=_TS,
    )

    with pytest.raises(ValueError, match="row missing owner value"):
        asyncio.run(repo.upsert_entity(entity))

    assert table.calls == []


# ---------------------------------------------------------------------------
# keyed rows
# ---------------------------------------------------------------------------


def test_upsert_carries_key_into_row_and_args() -> None:
    """A keyed upsert binds the key as $2."""
    import asyncio

    table = _make_table(fetch_returns=[{"key": "layout"}])
    repo = _make_repo(table)

    asyncio.run(
        repo.upsert("user", "u1", {"a": 1}, updated_at=_TS, key="layout")
    )

    _, args = _fetch_sql_args(table)
    assert args == ("u1", "layout", json.dumps({"a": 1}), _TS, 50)


def test_read_passes_key_through() -> None:
    """read() filters on the requested key."""
    import asyncio

    table = _make_table(seed={
        "r1": {
            "id": "r1",
            "user_id": "u1",
            "key": "layout",
            "payload": json.dumps({"a": 1}),
        }
    })
    repo = _make_repo(table)

    result = asyncio.run(repo.read("user", "u1", "layout"))

    assert result == {"a": 1}
    assert table.calls_of("select_row")[-1].kwargs["where"] == {
        "user_id": "u1",
        "key": "layout",
    }


def test_upsert_entity_carries_entity_key() -> None:
    """upsert_entity uses PreferencesEntity.key (None normalises to "")."""
    import asyncio

    from src.db.entities.preferences import PreferencesEntity

    table = _make_table(fetch_returns=[{"key": "theme"}])
    repo = _make_repo(table)
    entity = PreferencesEntity(
        scope="role",
        role_id="r-1",
        key="theme",
        payload={},
        updated_at=_TS,
    )

    asyncio.run(repo.upsert_entity(entity))

    _, args = _fetch_sql_args(table)
    assert args[1] == "theme"


# ---------------------------------------------------------------------------
# per-owner cap
# ---------------------------------------------------------------------------


def test_upsert_raises_when_no_row_is_returned() -> None:
    """A brand-new key past the cap inserts nothing -> limit error."""
    import asyncio

    table = _make_table(fetch_returns=[])
    repo = _make_repo(table)

    with pytest.raises(PreferencesLimitExceeded, match="maximum number"):
        asyncio.run(repo.upsert("user", "u1", {"a": 1}, key="extra"))


def test_upsert_does_not_raise_when_a_row_is_returned() -> None:
    """A normal insert/update that returns the key must not trip the cap."""
    import asyncio

    table = _make_table(fetch_returns=[{"key": "extra"}])
    repo = _make_repo(table)

    asyncio.run(repo.upsert("user", "u1", {"a": 1}, key="extra"))


def test_repo_rejects_non_positive_max_preferences() -> None:
    """The cap must be at least 1 row."""
    table = _make_table()
    with pytest.raises(ValueError, match="max_preferences must be >= 1"):
        _make_repo(table, max_preferences=0)
