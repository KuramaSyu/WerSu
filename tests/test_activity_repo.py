"""SQLite-backed tests for :class:`PostgresActivityRepo`.

These tests prove the SQL the repo builds is valid against a real
SQLite engine, that the WHERE / ORDER BY / LIMIT plumbing is wired
right, and that the full happy path (insert -> select -> edit ->
delete) round-trips through the activity filter builder.

Why SQLite rather than the existing Postgres testcontainer?  Because
the activity table is a plain Postgres table with no Postgres-specific
features the test needs.  The repo's SQL builder picks the placeholder
style from the table's bound builder, so the same
:class:`PostgresActivityRepo` exercises the SQLite code path here.

The test database is fresh per-test (in-memory); the ``activity``
table is created by the fixture with the same column shape the real
migration produces.  We omit the Postgres-only ENUM types and use
TEXT columns; the per-row target-shape validation lives in the repo
(``_validate_target_shape``) and runs in both dialects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator, List, Tuple

import uuid

import pytest

from src.api.activity import ActivityFilterBuilder
from src.db.entities.activity import (
    ActivityEntity,
    ActivityKind,
    ActivityScore,
)
from src.db.repos.activity.postgres import PostgresActivityRepo
from src.db.sql_builders import SqlBuilderFactory
from src.db.sqlite_database import SqliteDatabase
from src.db.table import Table
from src.utils.logging import logging_provider
from tests._fixtures_pkg.fakes import _TestDirectoryRepo


# Fixtures


@pytest.fixture
async def sqlite_db() -> AsyncGenerator[SqliteDatabase, None]:
    """A fresh in-memory SQLite database with an ``activity`` table.

    Schema mirrors the production migration closely enough to
    exercise the repo's SQL: same column names + types.  The target
    columns (``note_id`` / ``directory_id`` / ``role_id``) have no
    CHECK here -- the production schema dropped the CHECK and lets
    the application layer validate target shape.
    """
    db = SqliteDatabase()
    await db.init_db()
    await db.execute(
        """
        CREATE TABLE activity (
            id           TEXT PRIMARY KEY,
            actor_id     TEXT NULL,
            accessed_as  TEXT NOT NULL DEFAULT 'user',
            action       TEXT NOT NULL,
            note_id      TEXT NULL,
            directory_id TEXT NULL,
            role_id      TEXT NULL,
            at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata     TEXT NULL
        )
        """
    )
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def activity_table(sqlite_db: SqliteDatabase) -> Table:
    """An ``activity`` :class:`Table` bound to the SQLite fixture."""
    return Table(
        table_name="activity",
        logging_provider=logging_provider,
        db=sqlite_db,  # type: ignore[arg-type]
        id_fields=["id"],
        dialect="sqlite",
        builder=SqlBuilderFactory.create("sqlite", name="activity"),
    )


@pytest.fixture
def repo(activity_table: Table) -> PostgresActivityRepo:
    """A :class:`PostgresActivityRepo` wired to the SQLite table."""
    return PostgresActivityRepo(
        table=activity_table,
        logging_provider=logging_provider,
    )


# Helpers


async def _insert(
    repo: PostgresActivityRepo,
    *,
    action: ActivityKind,
    note_id: str | None = None,
    directory_id: str | None = None,
    role_id: str | None = None,
    actor_id: str | None = None,
    accessed_as: str = "user",
    metadata: dict | None = None,
    entity_id: str | None = None,
) -> ActivityEntity:
    """Convenience wrapper: insert an activity and return the entity.

    ``id`` is generated here -- SQLite's ``DEFAULT`` only accepts
    literals, not function calls, so the production ``uuidv7()`` is
    unavailable in tests.  ``metadata`` is serialised to JSON because
    the test schema stores metadata as ``TEXT``.
    """
    import json

    payload = metadata if metadata is not None else {}
    entity = ActivityEntity(
        id=entity_id or str(uuid.uuid4()),
        actor_id=actor_id,
        accessed_as=accessed_as,
        action=action,
        note_id=note_id,
        directory_id=directory_id,
        role_id=role_id,
        metadata=json.dumps(payload),
    )
    return await repo.add_activity(entity)


# add_activity


class TestAddActivity:
    """``add_activity`` validates and inserts a row."""

    @pytest.mark.asyncio
    async def test_insert_minimal_note_viewed(self, repo: PostgresActivityRepo) -> None:
        """A note-viewed row round-trips and gets an id + at back."""
        entity = await _insert(repo, action="note_viewed", note_id="n-1")

        assert entity.id is not None
        assert entity.action == "note_viewed"
        assert entity.note_id == "n-1"
        assert entity.directory_id is None
        assert entity.accessed_as == "user"

        rows = await repo.get_activities(
            ActivityFilterBuilder().use_history().build()
        )
        assert len(rows) == 1
        assert rows[0].id == entity.id

    @pytest.mark.asyncio
    async def test_insert_accessed_as_system(self, repo: PostgresActivityRepo) -> None:
        """``accessed_as="system"`` round-trips."""
        entity = await _insert(
            repo,
            action="note_edited",
            note_id="n-1",
            actor_id="alice",
            accessed_as="system",
        )
        assert entity.accessed_as == "system"

    @pytest.mark.asyncio
    async def test_insert_role_grant(self, repo: PostgresActivityRepo) -> None:
        """A ``role_grant`` row carries ``role_id`` and JSON metadata."""
        import json
        entity = await _insert(
            repo,
            action="role_grant",
            role_id="r-1",
            actor_id="alice",
            metadata={"subject_id": "bob", "role_name": "writer"},
        )

        assert entity.id is not None
        assert entity.role_id == "r-1"
        assert entity.note_id is None
        assert entity.directory_id is None
        assert entity.metadata == json.dumps(
            {"subject_id": "bob", "role_name": "writer"}
        )

    @pytest.mark.asyncio
    async def test_insert_rejects_missing_action(self, repo: PostgresActivityRepo) -> None:
        """``add_activity`` fails fast when ``action`` is not set."""
        with pytest.raises(ValueError, match="action is required"):
            await repo.add_activity(ActivityEntity(note_id="n-1"))

    @pytest.mark.asyncio
    async def test_insert_rejects_both_targets(self, repo: PostgresActivityRepo) -> None:
        """Note action rejects when directory_id is also set."""
        with pytest.raises(ValueError, match="requires note_id"):
            await repo.add_activity(
                ActivityEntity(action="note_viewed", note_id="n-1", directory_id="d-1")
            )

    @pytest.mark.asyncio
    async def test_insert_rejects_no_target(self, repo: PostgresActivityRepo) -> None:
        """Note action rejects when note_id is missing."""
        with pytest.raises(ValueError, match="requires note_id"):
            await repo.add_activity(ActivityEntity(action="note_viewed"))

    @pytest.mark.asyncio
    async def test_insert_rejects_role_action_with_note_id(
        self, repo: PostgresActivityRepo,
    ) -> None:
        """Role action rejects when note_id is set."""
        with pytest.raises(ValueError, match="requires role_id"):
            await repo.add_activity(
                ActivityEntity(action="role_grant", role_id="r-1", note_id="n-1")
            )


# get_activities (history)


class TestGetActivities:
    """``get_activities`` exercises history-mode SQL on SQLite."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rows(self, repo: PostgresActivityRepo) -> None:
        rows = await repo.get_activities(
            ActivityFilterBuilder().use_history().build()
        )
        assert rows == []

    @pytest.mark.asyncio
    async def test_filter_by_note_id(self, repo: PostgresActivityRepo) -> None:
        """Setting ``set_note`` restricts to that note only."""
        await _insert(repo, action="note_viewed", note_id="n-1")
        await _insert(repo, action="note_viewed", note_id="n-2")

        rows = await repo.get_activities(
            ActivityFilterBuilder().use_history().set_note("n-1").build()
        )
        assert len(rows) == 1
        assert rows[0].note_id == "n-1"

    @pytest.mark.asyncio
    async def test_filter_by_user(self, repo: PostgresActivityRepo) -> None:
        """Setting ``set_user`` restricts to that actor only."""
        await _insert(repo, action="note_viewed", note_id="n-1", actor_id="alice")
        await _insert(repo, action="note_viewed", note_id="n-2", actor_id="bob")

        rows = await repo.get_activities(
            ActivityFilterBuilder().use_history().set_user("alice").build()
        )
        assert len(rows) == 1
        assert rows[0].actor_id == "alice"

    @pytest.mark.asyncio
    async def test_filter_by_accessed_as(self, repo: PostgresActivityRepo) -> None:
        """``set_accessed_as("system")`` keeps only system rows."""
        await _insert(repo, action="note_viewed", note_id="n-1", accessed_as="user")
        await _insert(repo, action="note_viewed", note_id="n-2", accessed_as="system")

        rows = await repo.get_activities(
            ActivityFilterBuilder().use_history().set_accessed_as("system").build()
        )
        assert len(rows) == 1
        assert rows[0].note_id == "n-2"
        assert rows[0].accessed_as == "system"

    @pytest.mark.asyncio
    async def test_filter_by_action_set(self, repo: PostgresActivityRepo) -> None:
        """``set_action_set`` becomes ``action = ANY(...)``."""
        await _insert(repo, action="note_viewed", note_id="n-1")
        await _insert(repo, action="note_edited", note_id="n-1")
        await _insert(repo, action="note_deleted", note_id="n-1")

        rows = await repo.get_activities(
            ActivityFilterBuilder()
            .use_history()
            .set_action_set("note_viewed", "note_edited")
            .set_note("n-1")
            .build()
        )
        actions = sorted(r.action for r in rows)
        assert actions == ["note_edited", "note_viewed"]

    @pytest.mark.asyncio
    async def test_filter_by_time_window(self, repo: PostgresActivityRepo) -> None:
        """``set_days`` becomes ``at >= NOW() - INTERVAL 'N days'``."""
        await _insert(repo, action="note_viewed", note_id="n-1")
        await _insert(repo, action="note_viewed", note_id="n-2")
        await sqlite_db_backdate(repo, "n-2", days=60)

        rows = await repo.get_activities(
            ActivityFilterBuilder().use_history().set_days(30).build()
        )
        assert len(rows) == 1
        assert rows[0].note_id == "n-1"

    @pytest.mark.asyncio
    async def test_filter_by_directory_subtree(
        self, activity_table: Table,
    ) -> None:
        """``set_directory`` expands to the subtree via the directory repo."""
        directory_repo = _TestDirectoryRepo()
        directory_repo.subtree_by_root["d-root"] = (
            ["n-1", "n-2"],
            ["d-root", "d-2"],
        )
        repo = PostgresActivityRepo(
            table=activity_table,
            directory_repo=directory_repo,
            logging_provider=logging_provider,
        )

        await _insert(repo, action="note_viewed", note_id="n-1")  # in subtree
        await _insert(repo, action="note_viewed", note_id="n-2")  # in subtree
        await _insert(repo, action="note_viewed", note_id="n-3")  # outside subtree
        await _insert(repo, action="directory_created", directory_id="d-root")  # in
        await _insert(repo, action="directory_created", directory_id="d-2")    # in
        await _insert(repo, action="directory_created", directory_id="d-other")  # out

        rows = await repo.get_activities(
            ActivityFilterBuilder().use_history().set_directory("d-root").build()
        )
        note_ids = sorted(r.note_id or "" for r in rows)
        directory_ids = sorted(r.directory_id or "" for r in rows)
        assert "n-3" not in note_ids
        assert "n-1" in note_ids
        assert "n-2" in note_ids
        assert "d-root" in directory_ids
        assert "d-2" in directory_ids

    @pytest.mark.asyncio
    async def test_filter_combines_user_and_note(
        self, repo: PostgresActivityRepo,
    ) -> None:
        """AND-chain of multiple plain pairs lands in one WHERE clause."""
        await _insert(repo, action="note_viewed", note_id="n-1", actor_id="alice")
        await _insert(repo, action="note_viewed", note_id="n-1", actor_id="bob")
        await _insert(repo, action="note_viewed", note_id="n-2", actor_id="alice")

        rows = await repo.get_activities(
            ActivityFilterBuilder()
            .use_history()
            .set_note("n-1")
            .set_user("alice")
            .build()
        )
        assert len(rows) == 1
        assert rows[0].actor_id == "alice"
        assert rows[0].note_id == "n-1"

    @pytest.mark.asyncio
    async def test_filter_combines_note_and_directory(
        self, activity_table: Table,
    ) -> None:
        """``set_note`` + ``set_directory`` combine freely."""
        directory_repo = _TestDirectoryRepo()
        directory_repo.subtree_by_root["d-root"] = (
            ["n-1", "n-99"],  # n-99 in subtree but we'll filter it out
            ["d-root"],
        )
        repo = PostgresActivityRepo(
            table=activity_table,
            directory_repo=directory_repo,
            logging_provider=logging_provider,
        )

        await _insert(repo, action="note_viewed", note_id="n-1")  # both match
        await _insert(repo, action="note_viewed", note_id="n-99")  # dir only
        await _insert(repo, action="note_viewed", note_id="n-other")  # neither

        rows = await repo.get_activities(
            ActivityFilterBuilder()
            .use_history()
            .set_note("n-1")
            .set_directory("d-root")
            .build()
        )
        # ``set_note("n-1")`` constrains note_id; ``set_directory``
        # adds an OR over the subtree.  Both are AND'd at the outer
        # level, so a row needs ``note_id == "n-1"`` AND to live in
        # the subtree.  Only n-1 satisfies both.
        note_ids = [r.note_id for r in rows]
        assert note_ids == ["n-1"]

    @pytest.mark.asyncio
    async def test_multiple_directory_roots_combine(
        self, activity_table: Table,
    ) -> None:
        """Multiple ``set_directory`` calls accumulate into one IN-list."""
        directory_repo = _TestDirectoryRepo()
        directory_repo.subtree_by_root["d-a"] = (["n-a"], ["d-a"])
        directory_repo.subtree_by_root["d-b"] = (["n-b"], ["d-b"])
        repo = PostgresActivityRepo(
            table=activity_table,
            directory_repo=directory_repo,
            logging_provider=logging_provider,
        )

        await _insert(repo, action="note_viewed", note_id="n-a")
        await _insert(repo, action="note_viewed", note_id="n-b")
        await _insert(repo, action="note_viewed", note_id="n-other")

        rows = await repo.get_activities(
            ActivityFilterBuilder()
            .use_history()
            .set_directory("d-a")
            .set_directory("d-b")
            .build()
        )
        note_ids = sorted(r.note_id for r in rows)
        assert "n-other" not in note_ids
        assert "n-a" in note_ids
        assert "n-b" in note_ids

    @pytest.mark.asyncio
    async def test_limit_and_offset_paginate(
        self, repo: PostgresActivityRepo,
    ) -> None:
        """``set_limit`` / ``set_offset`` map to LIMIT / OFFSET."""
        for i in range(5):
            await _insert(repo, action="note_viewed", note_id=f"n-{i}")

        page1 = await repo.get_activities(
            ActivityFilterBuilder()
            .use_history()
            .set_limit(2)
            .set_offset(0)
            .build()
        )
        page2 = await repo.get_activities(
            ActivityFilterBuilder()
            .use_history()
            .set_limit(2)
            .set_offset(2)
            .build()
        )
        assert len(page1) == 2
        assert len(page2) == 2
        ids1 = {r.id for r in page1}
        ids2 = {r.id for r in page2}
        assert ids1.isdisjoint(ids2)


# get_most_used


class TestGetMostUsed:
    """``get_most_used`` exercises aggregate-mode SQL on SQLite."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rows(
        self, repo: PostgresActivityRepo,
    ) -> None:
        rows = await repo.get_most_used(
            ActivityFilterBuilder().show_most_used().build()
        )
        assert rows == []

    @pytest.mark.asyncio
    async def test_count_strategy_groups_by_note(
        self, repo: PostgresActivityRepo,
    ) -> None:
        """``count`` returns ``(note_id, score)`` pairs sorted desc."""
        await _insert(repo, action="note_viewed", note_id="n-1")  # 1 view
        await _insert(repo, action="note_viewed", note_id="n-2")  # 1 view
        await _insert(repo, action="note_viewed", note_id="n-2")  # 2 views
        await _insert(repo, action="note_viewed", note_id="n-2")  # 3 views

        rows = await repo.get_most_used(
            ActivityFilterBuilder().show_most_used().build()
        )
        assert rows[0].note_id == "n-2"
        assert rows[0].score == 3
        assert rows[1].note_id == "n-1"
        assert rows[1].score == 1

    @pytest.mark.asyncio
    async def test_most_used_respects_directory_filter(
        self, activity_table: Table,
    ) -> None:
        """Directory subtree filtering composes with the aggregate."""
        directory_repo = _TestDirectoryRepo()
        directory_repo.subtree_by_root["d-root"] = (
            ["n-1", "n-2"],
            ["d-root"],
        )
        repo = PostgresActivityRepo(
            table=activity_table,
            directory_repo=directory_repo,
            logging_provider=logging_provider,
        )

        await _insert(repo, action="note_viewed", note_id="n-1")
        await _insert(repo, action="note_viewed", note_id="n-1")
        await _insert(repo, action="note_viewed", note_id="n-3")

        rows = await repo.get_most_used(
            ActivityFilterBuilder()
            .show_most_used()
            .set_directory("d-root")
            .build()
        )
        note_ids = [r.note_id for r in rows]
        assert "n-3" not in note_ids
        assert "n-1" in note_ids


# edit_activity + remove_activity_by_id


class TestEditAndRemove:
    """``edit_activity`` and ``remove_activity_by_id`` round-trip."""

    @pytest.mark.asyncio
    async def test_edit_changes_metadata(
        self, repo: PostgresActivityRepo,
    ) -> None:
        """``edit_activity`` replaces the persisted columns."""
        import json
        from dataclasses import replace as _replace
        entity = await _insert(
            repo,
            action="note_viewed",
            note_id="n-1",
            metadata={"raw": "old"},
        )
        updated = await repo.edit_activity(
            _replace(entity, metadata=json.dumps({"raw": "new"}))
        )
        rows = await repo.get_activities(
            ActivityFilterBuilder().use_history().set_note("n-1").build()
        )
        assert len(rows) == 1
        assert rows[0].metadata != entity.metadata

    @pytest.mark.asyncio
    async def test_edit_requires_id(self, repo: PostgresActivityRepo) -> None:
        with pytest.raises(ValueError, match="id is required"):
            await repo.edit_activity(ActivityEntity(action="note_viewed"))

    @pytest.mark.asyncio
    async def test_edit_unknown_id_raises(self, repo: PostgresActivityRepo) -> None:
        with pytest.raises(ValueError, match="activity not found"):
            await repo.edit_activity(
                ActivityEntity(id="does-not-exist", actor_id="alice")
            )

    @pytest.mark.asyncio
    async def test_remove_deletes_row(self, repo: PostgresActivityRepo) -> None:
        entity = await _insert(repo, action="note_viewed", note_id="n-1")
        await repo.remove_activity_by_id(entity.id)
        rows = await repo.get_activities(
            ActivityFilterBuilder().use_history().set_note("n-1").build()
        )
        assert rows == []

    @pytest.mark.asyncio
    async def test_remove_unknown_id_raises(self, repo: PostgresActivityRepo) -> None:
        with pytest.raises(ValueError, match="activity not found"):
            await repo.remove_activity_by_id("does-not-exist")


# helpers


async def sqlite_db_backdate(repo: PostgresActivityRepo, note_id: str, *, days: int) -> None:
    """Set the ``at`` column of a row to ``NOW() - days`` for testing.

    Goes through the raw ``sqlite3.Connection.execute`` so we get
    parameter binding; :meth:`SqliteDatabase.execute` uses
    ``executescript`` and drops bound params.
    """
    db = repo._table.db  # type: ignore[attr-defined]
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_iso = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    import asyncio
    await asyncio.to_thread(
        db.connection.execute,
        "UPDATE activity SET at = ? WHERE note_id = ?",
        (cutoff_iso, note_id),
    )
    await asyncio.to_thread(db.connection.commit)