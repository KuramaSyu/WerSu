"""Regression tests for :class:`src.db.repos.note.content.NoteContentPostgresRepo`.

The repo sits between :class:`src.db.entities.note.metadata.NoteEntity`
(which carries everything -- `title`, `content`, `parent_dir_id`,
`embeddings`, `permissions`, ...) and the narrow ``note.content``
table which only knows about five columns: ``id``, ``title``,
``content``, ``updated_at``, ``author_id``.

The legacy code forwarded the whole entity to the underlying
:class:`Table` insert / update, which translated every dict key
into a column reference.  The orchestrator-driven import path
(`BookstackBookImport._rewrite_cross_references`) re-sends a
fully-populated :class:`NoteEntity` after insert -- if the repo
forwards the entity as-is, the SQL ends up referencing
``parent_dir_id`` (and friends) which the ``content`` table does
not have.  Postgres then rejects the statement with::

    column "parent_dir_id" of relation "content" does not exist

These tests pin the column allow-list at the repo boundary so
that any future caller passing a wider entity cannot regress the
SQL.  They run against the in-memory :class:`SqliteDatabase`
shim -- no Postgres required -- and inspect the actual SQL the
repo emits via :attr:`Table._executed_sql`.
"""

from __future__ import annotations

from typing import AsyncGenerator, List

import pytest

from src.api.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.sql_builders import SqlBuilderFactory
from src.db.sqlite_database import SqliteDatabase
from src.db.table import Table
from src.utils.logging import logging_provider


# The five columns the `note.content` table actually has.  Tests
# below assert that the SQL the repo emits references *only* these --
# even when the entity carries extra fields like `parent_dir_id`,
# `embeddings`, or `permissions`.
CONTENT_COLUMNS = {"id", "title", "content", "updated_at", "author_id"}


# ---- fixtures ----------------------------------------------------------


@pytest.fixture
async def content_db() -> AsyncGenerator[SqliteDatabase, None]:
    """A fresh SQLite database with the ``note.content`` schema."""
    db = SqliteDatabase()
    await db.init_db()
    await db.execute(
        """
        CREATE TABLE content (
            id TEXT PRIMARY KEY,
            title TEXT,
            content TEXT,
            updated_at TIMESTAMP,
            author_id TEXT NOT NULL
        )
        """
    )
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def content_table(content_db: SqliteDatabase) -> Table:
    """A :class:`Table` bound to the ``content`` schema above."""
    return Table(
        table_name="content",
        logging_provider=logging_provider,
        db=content_db,  # type: ignore[arg-type]  -- SqliteDatabase quacks like Database
        id_fields=["id"],
        dialect="sqlite",
        builder=SqlBuilderFactory.create("sqlite", name="content"),
    )


@pytest.fixture
def repo(content_table: Table) -> NoteContentPostgresRepo:
    """The repo under test, wired to the SQLite content table."""
    return NoteContentPostgresRepo(content_table)


def _executed_columns(table: Table) -> List[str]:
    """Return the column names referenced by the last SQL the table ran.

    Parses ``Table._executed_sql`` (the cache populated by
    :meth:`Table._log_statement`) so tests can assert on what the
    repo actually emitted, not just on the dict the repo built.
    """
    sql = table._executed_sql
    # ``_executed_sql`` looks like
    # ``SQL:\nINSERT INTO content(...) VALUES (...)\nWITH VALUES: [...]``
    # or
    # ``SQL:\nUPDATE content\nSET ...\nWHERE ...\nRETURNING ...\nWITH VALUES: [...]``
    upper = sql.upper()
    if "INSERT INTO" in upper:
        start = upper.index("(") + 1
        end = upper.index(")", start)
        raw = sql[start:end]
        return [col.strip().strip('"') for col in raw.split(",") if col.strip()]
    if "UPDATE" in upper:
        # SET clause lives between ``UPDATE <table> SET`` and the
        # next ``WHERE`` / ``RETURNING`` keyword.  Match the start
        # of ``SET`` against ``\nSET`` or `` SET`` because the
        # builder joins fragments without surrounding whitespace.
        set_marker = upper.find("\nSET")
        if set_marker == -1:
            set_marker = upper.find(" SET")
        tail = upper[set_marker:]
        # stop at the first clause boundary after SET
        end = len(tail)
        for marker in ("\nWHERE", " RETURNING"):
            idx = tail.find(marker)
            if 0 <= idx < end:
                end = idx
        clause = tail[len("\nSET"):end] if tail.startswith("\nSET") else tail[4:end]
        cols: List[str] = []
        for piece in clause.split(","):
            head = piece.split("=", 1)[0].strip().strip('"')
            if head:
                cols.append(head)
        return [c.lower() for c in cols]
    return []


# ---- insert ------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_emits_only_known_columns(
    repo: NoteContentPostgresRepo,
    content_table: Table,
) -> None:
    """A ``NoteEntity`` carrying every field must still only insert
    into the five known columns of ``note.content``."""
    note = NoteEntity(
        note_id="n-1",
        title="hello",
        content="body",
        updated_at=None,
        author_id="u-1",
        # entity-only fields that the content table does not have:
        parent_dir_id="dir-1",
        embeddings=["bogus"],  # type: ignore[list-item]
        permissions=["bogus"],  # type: ignore[list-item]
    )

    await repo.insert(note)

    columns = _executed_columns(content_table)
    assert columns, "expected the table to have captured an INSERT"
    assert set(columns) == {"id", "title", "content", "updated_at", "author_id"}
    assert "parent_dir_id" not in columns
    assert "embeddings" not in columns
    assert "permissions" not in columns


@pytest.mark.asyncio
async def test_insert_drops_undefined_values(
    repo: NoteContentPostgresRepo,
    content_table: Table,
) -> None:
    """`UNDEFINED` values fall out of the SQL so the table never
    sees NULL-from-missing rather than NULL-from-cleared."""
    note = NoteEntity(
        note_id="n-1",
        title="t",
        content="c",
        author_id="u-1",
        # updated_at left as UNDEFINED -> dropped, not stored as NULL
    )

    await repo.insert(note)

    columns = _executed_columns(content_table)
    assert "updated_at" not in columns, (
        f"updated_at should have been dropped; columns={columns}; "
        f"raw SQL = {content_table._executed_sql!r}"
    )
    # only the four set columns plus ``id`` show up
    assert set(columns) == {"id", "title", "content", "author_id"}


@pytest.mark.asyncio
async def test_insert_round_trips_row(
    repo: NoteContentPostgresRepo,
    content_table: Table,
) -> None:
    """An inserted note can be read back from the SQLite-backed table."""
    note = NoteEntity(
        note_id="n-1",
        title="hello",
        content="body",
        author_id="u-1",
    )

    await repo.insert(note)

    rows = await content_table.select(where={"id": "n-1"})
    assert rows == [{"id": "n-1", "title": "hello", "content": "body",
                    "updated_at": None, "author_id": "u-1"}]


# ---- update ------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_emits_only_known_columns(
    repo: NoteContentPostgresRepo,
    content_table: Table,
) -> None:
    """An update with extra entity fields still only touches the
    five known columns -- this is the path that produced the
    ``column "parent_dir_id" of relation "content" does not exist``
    error in the BookStack import when the orchestrator re-sent a
    fully-populated ``NoteEntity`` after the rewrite pass.

    The repo's ``update()`` has an ``assert isinstance(record, Record)``
    guard that fails against the SQLite shim (which returns a dict)
    -- the SQL it emits is the actual contract under test, so we
    capture :attr:`Table._executed_sql` before the assertion fires
    and inspect that instead.
    """
    # Seed a row so the UPDATE has something to match.
    seed = NoteEntity(
        note_id="n-1",
        title="hello",
        content="body",
        author_id="u-1",
    )
    await repo.insert(seed)

    note = NoteEntity(
        note_id="n-1",
        title="hello v2",
        content="body v2",
        author_id="u-1",
        parent_dir_id="dir-1",
        embeddings=["bogus"],  # type: ignore[list-item]
        permissions=["bogus"],  # type: ignore[list-item]
    )
    where = NoteEntity(note_id="n-1")

    with pytest.raises((AssertionError, Exception)):
        await repo.update(set=note, where=where)

    raw_sql = content_table._executed_sql
    assert "parent_dir_id" not in raw_sql, raw_sql
    assert "embeddings" not in raw_sql, raw_sql
    assert "permissions" not in raw_sql, raw_sql

    columns = _executed_columns(content_table)
    # ``_executed_columns`` parses the SET clause; verify only known
    # columns appear there.
    for col in columns:
        assert col in CONTENT_COLUMNS, (
            f"unexpected column in update SET clause: {col!r}; "
            f"raw SQL = {raw_sql!r}"
        )


@pytest.mark.asyncio
async def test_update_with_undefined_fields_drops_them(
    repo: NoteContentPostgresRepo,
    content_table: Table,
) -> None:
    """`UNDEFINED` fields are stripped from the SET clause so a partial
    update doesn't accidentally overwrite existing values with NULL.

    The repo's ``update()`` has an ``assert isinstance(record, Record)``
    guard that fails against the SQLite shim (which returns a dict)
    -- we capture :attr:`Table._executed_sql` before the assertion
    fires and inspect that instead.
    """
    # Seed a row so the UPDATE has something to match.
    seed = NoteEntity(
        note_id="n-1",
        title="original",
        content="original",
        author_id="u-1",
    )
    await repo.insert(seed)

    note = NoteEntity(
        note_id="n-1",
        title=UNDEFINED,         # don't touch existing title
        content="body v2",       # only update content
        author_id=UNDEFINED,     # don't touch existing author
        parent_dir_id="dir-1",
    )
    where = NoteEntity(note_id="n-1")

    with pytest.raises((AssertionError, Exception)):
        await repo.update(set=note, where=where)

    raw_sql = content_table._executed_sql
    assert "parent_dir_id" not in raw_sql, raw_sql
    # title/author_id should be dropped because they are UNDEFINED
    # and so should not appear in the SET clause column list.
    set_clause = raw_sql.split("WHERE")[0]
    assert "title" not in set_clause, set_clause
    assert "author_id" not in set_clause, set_clause


# ---- delete ------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_where_clause_only_uses_id(
    repo: NoteContentPostgresRepo,
    content_table: Table,
) -> None:
    """``delete()`` forwards only the entity fields that match table
    columns -- `parent_dir_id`, `embeddings`, `permissions` must
    never appear in the DELETE's WHERE clause."""
    # Seed a row so the DELETE has something to match.
    seed = NoteEntity(
        note_id="n-1",
        title="t",
        author_id="u-1",
    )
    await repo.insert(seed)

    note = NoteEntity(
        note_id="n-1",
        title="t",
        author_id="u-1",
        parent_dir_id="dir-1",
        embeddings=["bogus"],  # type: ignore[list-item]
        permissions=["bogus"],  # type: ignore[list-item]
    )

    await repo.delete(note)

    raw_sql = content_table._executed_sql
    assert "parent_dir_id" not in raw_sql
    assert "embeddings" not in raw_sql
    assert "permissions" not in raw_sql
    # the WHERE clause should be a single PK match on `id`
    upper = raw_sql.upper()
    where_idx = upper.index("WHERE")
    where_clause = raw_sql[where_idx:]
    assert "id" in where_clause