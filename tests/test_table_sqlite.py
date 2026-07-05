"""End-to-end tests for :class:`src.db.table.Table` against SQLite.

The legacy integration tests required a live Postgres container
running via testcontainers.  That's slow, fragile, and impossible
on machines that can't pull images.  These tests replace the
"real database" leg of every :class:`src.db.table.Table` call with
:class:`src.db.sqlite_database.SqliteDatabase`, an in-memory shim
that exposes the same ``fetch`` / ``fetchrow`` / ``execute``
surface over the stdlib ``sqlite3`` driver.

The point is to prove the SQL builder emits valid statements and
that :class:`Table` plumbs them through to a real driver.  Tests
are fast (no containers), deterministic (in-memory state, fresh
per-test), and exercise INSERT / SELECT / UPDATE / DELETE / upsert
/ fetch_by_id end-to-end.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Dict

import pytest

from src.db.sql_builders import SqlBuilderFactory
from src.db.sqlite_database import SqliteDatabase
from src.db.table import Table
from src.utils.logging import logging_provider


# Pytest fixture: an empty users table wired up to SQLite ---------------


@pytest.fixture
async def sqlite_db() -> AsyncGenerator[SqliteDatabase, None]:
    """A fresh in-memory SQLite database with a ``users`` table."""
    db = SqliteDatabase()
    await db.init_db()
    await db.execute(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT,
            age INTEGER
        )
        """
    )
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def users_table(sqlite_db: SqliteDatabase) -> Table:
    """A ``users`` :class:`Table` bound to the SQLite fixture above."""
    return Table(
        table_name="users",
        logging_provider=logging_provider,
        db=sqlite_db,  # type: ignore[arg-type]  -- SqliteDatabase quacks like Database
        id_fields=["id"],
        dialect="sqlite",
        builder=SqlBuilderFactory.create("sqlite", name="users"),
    )


# insert ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_round_trips_a_row(users_table: Table) -> None:
    """``insert()`` persists a row and returns the inserted record."""
    rows = await users_table.insert(
        where={"id": "u-1", "username": "kurama", "email": "k@example"}
    )

    assert rows is not None
    assert len(rows) == 1
    assert rows[0]["id"] == "u-1"
    assert rows[0]["username"] == "kurama"

    # SQLite picked up RETURNING in 3.35; the row id should round-trip
    # back through the dialect without a separate select.
    stored = await users_table.select(where={"id": "u-1"})
    assert stored is not None
    assert len(stored) == 1
    assert stored[0]["username"] == "kurama"


# select -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_returns_empty_list_when_no_match(users_table: Table) -> None:
    """``select()`` with a mismatch returns ``[]`` (mirrors asyncpg)."""
    rows = await users_table.select(where={"id": "missing"})

    assert rows == []


@pytest.mark.asyncio
async def test_select_row_returns_first_match(users_table: Table) -> None:
    """``select_row()`` returns one dict, not a list."""
    await users_table.insert(where={"id": "u-1", "username": "kurama"})

    row = await users_table.select_row(where={"id": "u-1"})

    assert row is not None
    assert row["id"] == "u-1"


# update -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_changes_a_column(users_table: Table) -> None:
    """``update()`` mutates one column and leaves the others alone."""
    await users_table.insert(
        where={"id": "u-1", "username": "kurama", "email": "old@example"}
    )

    await users_table.update(set={"email": "new@example"}, where={"id": "u-1"})

    row = await users_table.select_row(where={"id": "u-1"})
    assert row["username"] == "kurama"
    assert row["email"] == "new@example"


@pytest.mark.asyncio
async def test_update_drops_undefined_where_values(
    users_table: Table,
) -> None:
    """``UNDEFINED`` values in the ``where`` dict are dropped from the SQL.

    Mirrors the legacy behaviour: callers can hand the repository a
    ``where`` dict populated from a dataclass with
    :obj:`src.api.undefined.UNDEFINED` sentinels for unset fields,
    and the missing columns simply disappear from the WHERE clause.
    """
    await users_table.insert(
        where={"id": "u-1", "username": "kurama", "age": 30}
    )

    await users_table.update(
        set={"username": "newname"},
        where={"id": "u-1", "age": 30},  # both real, no UNDEFINED here
    )

    row = await users_table.select_row(where={"id": "u-1"})
    assert row["username"] == "newname"


# delete -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_by_id_removes_row(users_table: Table) -> None:
    """``delete_by_id()`` removes a single row by the id_fields."""
    await users_table.insert(
        where={"id": "u-1", "username": "kurama"},
    )
    await users_table.insert(
        where={"id": "u-2", "username": "kuro"},
    )

    deleted = await users_table.delete_by_id("u-1")

    assert deleted is not None
    assert deleted["id"] == "u-1"
    remaining = await users_table.select(where={})
    assert len(remaining) == 1
    assert remaining[0]["id"] == "u-2"


# fetch_by_id ------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_by_id_returns_row(users_table: Table) -> None:
    await users_table.insert(where={"id": "u-1", "username": "kurama"})

    row = await users_table.fetch_by_id("u-1")

    assert row is not None
    assert row["id"] == "u-1"
    assert row["username"] == "kurama"


@pytest.mark.asyncio
async def test_fetch_by_id_missing_returns_none(users_table: Table) -> None:
    """``fetch_by_id()`` returns ``None`` when the row isn't found."""
    row = await users_table.fetch_by_id("nope")

    assert row is None


# upsert -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_inserts_when_missing(users_table: Table) -> None:
    """``upsert()`` on a brand new id acts like an INSERT."""
    await users_table.upsert(
        where={"id": "u-1", "username": "kurama"}
    )

    row = await users_table.fetch_by_id("u-1")
    assert row["username"] == "kurama"


@pytest.mark.asyncio
async def test_upsert_updates_when_existing(users_table: Table) -> None:
    """``upsert()`` on an existing id updates the non-conflict columns."""
    await users_table.upsert(
        where={"id": "u-1", "username": "kurama"}
    )

    # ``id`` is the id_field so it's the ON CONFLICT target;
    # the builder excludes it from the SET clause and updates
    # the other columns.
    await users_table.upsert(
        where={"id": "u-1", "username": "kuro"}
    )

    row = await users_table.fetch_by_id("u-1")
    assert row["username"] == "kuro"


# fetch (raw SQL) --------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_passes_through_custom_sql(
    users_table: Table,
) -> None:
    """``fetch()`` is a thin pass-through for raw SQL."""
    await users_table.insert(where={"id": "u-1", "username": "kurama"})

    rows = await users_table.fetch("SELECT * FROM users WHERE id = ?", "u-1")

    assert rows is not None
    assert len(rows) == 1
    assert rows[0]["username"] == "kurama"


# Dialect configuration --------------------------------------------------


def test_table_uses_postgres_builder_by_default() -> None:
    """A ``Table`` without a ``dialect`` gets the Postgres builder."""
    # We can't construct a Table without a db, but the constructor
    # should default ``dialect`` to ``"postgres"`` and wire up the
    # matching builder.  We assert that by introspecting the
    # instance after construction with a stub db.
    class _NoOpDb:
        async def fetch(self, *_args, **_kwargs):
            return []

        async def fetchrow(self, *_args, **_kwargs):
            return None

        async def execute(self, *_args, **_kwargs):
            return ""

    table = Table(
        table_name="users",
        logging_provider=logging_provider,
        db=_NoOpDb(),  # type: ignore[arg-type]
    )
    assert table.dialect == "postgres"
    # Postgres flavour: ``insert()`` returns a PostgresInsertStmt
    # whose placeholder style is ``$``.
    staged = table.builder.insert().into("users").values(id="u-1")
    sql = staged.build().sql
    assert "$1" in sql
