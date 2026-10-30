"""Unit tests for :class:`src.db.repos.tag.postgres.PostgresTagRepo`.

The real repo is driven against a :class:`RecordingTable` (for the
``TableABC`` paths) and the queued-response
:class:`tests._fixtures_pkg.fakes._FakeDatabase` (for the raw-SQL
paths), so the SQL generation and entity mapping can be verified
without booting Postgres.  Association behaviour against a real
database lives in ``tests/integration/test_notes_repo.py``.
"""

from __future__ import annotations

import pytest

from src.db.repos.tag.postgres import PostgresTagRepo
from tests._fixtures_pkg.fakes import _FakeDatabase
from tests.stubs.recording_table import RecordingTable


def _make_repo(
    *,
    db: _FakeDatabase | None = None,
    tag_table: RecordingTable | None = None,
) -> tuple[PostgresTagRepo, RecordingTable, _FakeDatabase]:
    """Build a real repo over recording fakes."""
    tags = tag_table if tag_table is not None else RecordingTable("note.tag")
    note_tags = RecordingTable("note.note_tag", id_fields=["note_id", "tag_id"])
    directory_tags = RecordingTable(
        "note.directory_tag", id_fields=["directory_id", "tag_id"],
    )
    database = db if db is not None else _FakeDatabase()
    repo = PostgresTagRepo(
        tag_table=tags,
        note_tag_table=note_tags,
        directory_tag_table=directory_tags,
        db=database,
    )
    return repo, tags, database


# ---- create ---------------------------------------------------------------


async def test_create_tag_inserts_row_and_maps_entity() -> None:
    repo, tags, _ = _make_repo()

    tag = await repo.create_tag("python", "Python")

    assert tag.id == "row-1"
    assert tag.slug == "python"
    assert tag.display_name == "Python"
    insert = tags.calls_of("insert")[0]
    assert insert.kwargs["where"] == {
        "slug": "python", "display_name": "Python",
    }


async def test_create_tag_rejects_empty_slug() -> None:
    repo, _, _ = _make_repo()
    with pytest.raises(ValueError, match="slug is required"):
        await repo.create_tag("", "Python")


async def test_create_tag_rejects_empty_display_name() -> None:
    repo, _, _ = _make_repo()
    with pytest.raises(ValueError, match="display_name is required"):
        await repo.create_tag("python", "")


async def test_create_tag_raises_when_insert_returns_nothing() -> None:
    class _NoRowInsertTable(RecordingTable):
        async def insert(self, where, returning="*", on_conflict=""):
            return []

    repo, _, _ = _make_repo(tag_table=_NoRowInsertTable("note.tag"))
    with pytest.raises(RuntimeError, match="Failed to create tag"):
        await repo.create_tag("python", "Python")


# ---- read -----------------------------------------------------------------


async def test_get_tag_by_id_returns_entity() -> None:
    repo, tags, _ = _make_repo()
    tags.stored["t-1"] = {
        "id": "t-1", "slug": "python", "display_name": "Python",
    }

    tag = await repo.get_tag_by_id("t-1")

    assert tag is not None
    assert tag.id == "t-1"
    assert tag.slug == "python"


async def test_get_tag_by_id_returns_none_when_missing() -> None:
    repo, _, _ = _make_repo()
    assert await repo.get_tag_by_id("missing") is None


async def test_list_tags_queries_and_maps_rows() -> None:
    db = _FakeDatabase()
    db.fetch_responses.append(
        [
            {"id": "t-1", "slug": "alpha", "display_name": "Alpha"},
            {"id": "t-2", "slug": "zeta", "display_name": "Zeta"},
        ]
    )
    repo, _, _ = _make_repo(db=db)

    tags = await repo.list_tags()

    assert [t.slug for t in tags] == ["alpha", "zeta"]
    sql, _args = db.fetch_calls[-1]
    assert "FROM note.tag" in sql
    assert "ORDER BY slug ASC" in sql


async def test_list_tags_returns_empty_on_no_rows() -> None:
    db = _FakeDatabase()
    db.fetch_responses.append([])
    repo, _, _ = _make_repo(db=db)
    assert await repo.list_tags() == []


# ---- update ---------------------------------------------------------------


async def test_update_tag_issues_partial_update() -> None:
    db = _FakeDatabase()
    db.fetchrow_responses.append(
        {"id": "t-1", "slug": "python", "display_name": "Python 3"}
    )
    repo, _, _ = _make_repo(db=db)

    tag = await repo.update_tag("t-1", display_name="Python 3")

    assert tag is not None
    assert tag.display_name == "Python 3"
    sql, args = db.fetchrow_calls[-1]
    assert "UPDATE note.tag" in sql
    assert "display_name = $2" in sql
    assert args == ("t-1", "Python 3")


async def test_update_tag_without_fields_reads_existing_row() -> None:
    repo, tags, _ = _make_repo()
    tags.stored["t-1"] = {
        "id": "t-1", "slug": "python", "display_name": "Python",
    }

    tag = await repo.update_tag("t-1")

    assert tag is not None
    assert tag.slug == "python"
    assert tags.calls_of("fetch_by_id")


async def test_update_tag_returns_none_when_missing() -> None:
    db = _FakeDatabase()
    db.fetchrow_responses.append(None)
    repo, _, _ = _make_repo(db=db)
    assert await repo.update_tag("missing", slug="x") is None


# ---- delete ---------------------------------------------------------------


async def test_delete_tag_removes_row() -> None:
    repo, tags, _ = _make_repo()
    tags.stored["t-1"] = {
        "id": "t-1", "slug": "python", "display_name": "Python",
    }

    assert await repo.delete_tag("t-1") is True
    delete = tags.calls_of("delete")[0]
    assert delete.kwargs["where"] == {"id": "t-1"}


async def test_delete_tag_returns_false_when_missing() -> None:
    repo, _, _ = _make_repo()
    assert await repo.delete_tag("missing") is False


async def test_delete_tag_rejects_empty_id() -> None:
    repo, _, _ = _make_repo()
    with pytest.raises(ValueError, match="tag_id is required"):
        await repo.delete_tag("")
