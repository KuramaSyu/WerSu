"""Tests for :class:`DefaultActivityStatisticsService`.

The activity repo is wired against an in-memory SQLite database (same
fixture as :mod:`tests.test_activity_repo`).  Permission checks and
directory resolution are stubbed with lightweight fakes so we can
exercise every branch the service offers without spinning up SpiceDB.
"""

from __future__ import annotations

from typing import AsyncGenerator, List, Optional

import pytest

from src.api.undefined import UNDEFINED
from src.db.repos.activity.postgres import PostgresActivityRepo
from src.db.sql_builders import SqlBuilderFactory
from src.db.sqlite_database import SqliteDatabase
from src.db.table import Table
from src.services.activity_statistics_service import (
    DefaultActivityStatisticsService,
)
from src.utils.logging import logging_provider
from tests._fixtures_pkg.fakes import _TestDirectoryRepo
from tests.stubs.user_context import _UserContext as _FakeUserContext
from tests.stubs.view_permission_repo import _FakeViewPermissionRepo as _FakePermissionRepo


# SQLite + repo fixture (lifted from test_activity_repo)


@pytest.fixture
async def sqlite_db() -> AsyncGenerator[SqliteDatabase, None]:
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
    return Table(
        table_name="activity",
        logging_provider=logging_provider,
        db=sqlite_db,  # type: ignore[arg-type]
        id_fields=["id"],
        dialect="sqlite",
        builder=SqlBuilderFactory.create("sqlite", name="activity"),
    )


@pytest.fixture
def directory_repo() -> _TestDirectoryRepo:
    """Directory repo whose ``resolve_subtree`` returns the root alone.

    Tests seed ``user_to_directory_ids`` and ``subtree_by_root`` as
    needed so the service's "all visible dirs" path can resolve.
    """
    repo = _TestDirectoryRepo()
    # Default: alice can view the two root dirs the statistics tests
    # operate on.  Each test that needs a custom mapping overrides this.
    repo.user_to_directory_ids["alice"] = ["d-root", "d-other"]
    repo.subtree_by_root["d-root"] = (["n-1", "n-2"], ["d-root"])
    repo.subtree_by_root["d-other"] = (["n-other"], ["d-other"])
    return repo


@pytest.fixture
def activity_repo(
    activity_table: Table, directory_repo: _TestDirectoryRepo,
) -> PostgresActivityRepo:
    return PostgresActivityRepo(
        table=activity_table,
        directory_repo=directory_repo,
        logging_provider=logging_provider,
    )


@pytest.fixture
def alice() -> _FakeUserContext:
    return _FakeUserContext(user_id="alice")


async def _insert(
    repo: PostgresActivityRepo,
    *,
    action: str,
    note_id: Optional[str] = None,
    directory_id: Optional[str] = None,
    actor_id: str = "alice",
) -> None:
    from src.api.undefined import UNDEFINED
    from src.db.entities.activity import ActivityEntity
    import uuid
    await repo.add_activity(
        ActivityEntity(
            id=str(uuid.uuid4()),
            actor_id=actor_id,
            accessed_as="user",
            action=action,  # type: ignore[arg-type]
            note_id=note_id,
            directory_id=directory_id,
            metadata="{}",
        )
    )


# Permission gating


class TestPermissionGating:
    """The service refuses to query data the actor can't view."""

    @pytest.mark.asyncio
    async def test_get_history_rejects_note_actor_cannot_view(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        perms = _FakePermissionRepo(viewable_note_ids=[])  # alice can't view any
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        with pytest.raises(PermissionError, match="cannot view note"):
            await svc.get_history(alice, note_id="n-1")

    @pytest.mark.asyncio
    async def test_get_history_rejects_directory_actor_cannot_view(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        perms = _FakePermissionRepo(viewable_directory_ids=[])
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        with pytest.raises(PermissionError, match="cannot view directory"):
            await svc.get_history(alice, directory_id="d-1")

    @pytest.mark.asyncio
    async def test_get_most_used_rejects_note_actor_cannot_view(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        perms = _FakePermissionRepo(viewable_note_ids=[])
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        with pytest.raises(PermissionError):
            await svc.get_most_used(alice, note_id="n-1")


# History queries


class TestGetHistory:
    """``get_history`` translates kwargs into the right filter."""

    @pytest.mark.asyncio
    async def test_filters_by_note_id_when_allowed(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        perms = _FakePermissionRepo(viewable_note_ids=["n-1"])
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        await _insert(activity_repo, action="note_viewed", note_id="n-1")
        await _insert(activity_repo, action="note_viewed", note_id="n-2")

        rows = await svc.get_history(alice, note_id="n-1")
        assert len(rows) == 1
        assert rows[0].note_id == "n-1"

    @pytest.mark.asyncio
    async def test_filters_by_actor_id(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        perms = _FakePermissionRepo(viewable_note_ids=["n-1", "n-2"])
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        await _insert(activity_repo, action="note_viewed", note_id="n-1", actor_id="alice")
        await _insert(activity_repo, action="note_viewed", note_id="n-2", actor_id="bob")

        rows = await svc.get_history(alice, note_id="n-1", actor_id="alice")
        assert len(rows) == 1
        assert rows[0].actor_id == "alice"

    @pytest.mark.asyncio
    async def test_filters_by_action_set(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        perms = _FakePermissionRepo(viewable_note_ids=["n-1"])
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        await _insert(activity_repo, action="note_viewed", note_id="n-1")
        await _insert(activity_repo, action="note_edited", note_id="n-1")
        await _insert(activity_repo, action="note_deleted", note_id="n-1")

        rows = await svc.get_history(
            alice, note_id="n-1",
            actions=["note_viewed", "note_edited"],
        )
        assert sorted(r.action for r in rows) == ["note_edited", "note_viewed"]

    @pytest.mark.asyncio
    async def test_pagination_via_limit_and_offset(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        perms = _FakePermissionRepo(viewable_note_ids=["n-1"])
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        for _ in range(5):
            await _insert(activity_repo, action="note_viewed", note_id="n-1")

        page1 = await svc.get_history(alice, note_id="n-1", limit=2, offset=0)
        page2 = await svc.get_history(alice, note_id="n-1", limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert {r.id for r in page1}.isdisjoint({r.id for r in page2})


# "Everything visible" -> directory resolution


class TestVisibleDirectoryResolution:
    """When neither target is set, the service resolves visible dirs."""

    @pytest.mark.asyncio
    async def test_no_targets_resolves_to_visible_dirs(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        # alice can see d-a, d-b
        directory_repo.user_to_directory_ids["alice"] = ["d-a", "d-b"]
        directory_repo.subtree_by_root["d-a"] = (["n-1"], ["d-a"])
        directory_repo.subtree_by_root["d-b"] = (["n-2"], ["d-b"])

        perms = _FakePermissionRepo()
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        await _insert(activity_repo, action="note_viewed", note_id="n-1")  # in d-a
        await _insert(activity_repo, action="note_viewed", note_id="n-2")  # in d-b
        await _insert(activity_repo, action="note_viewed", note_id="n-3")  # outside

        rows = await svc.get_history(alice)
        note_ids = sorted(r.note_id for r in rows)
        assert "n-3" not in note_ids
        assert "n-1" in note_ids
        assert "n-2" in note_ids


# Most-used


class TestGetMostUsed:
    """``get_most_used`` exercises the aggregate path."""

    @pytest.mark.asyncio
    async def test_count_strategy(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        perms = _FakePermissionRepo(viewable_note_ids=["n-1", "n-2"])
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        await _insert(activity_repo, action="note_viewed", note_id="n-1")
        await _insert(activity_repo, action="note_viewed", note_id="n-2")
        await _insert(activity_repo, action="note_viewed", note_id="n-2")
        await _insert(activity_repo, action="note_viewed", note_id="n-2")

        rows = await svc.get_most_used(alice)
        assert rows[0].note_id == "n-2"
        assert rows[0].score == 3

    @pytest.mark.asyncio
    async def test_log_count_algorithm(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        import math
        perms = _FakePermissionRepo(viewable_note_ids=["n-1", "n-2"])
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        await _insert(activity_repo, action="note_viewed", note_id="n-1")
        await _insert(activity_repo, action="note_viewed", note_id="n-2")
        for _ in range(4):
            await _insert(activity_repo, action="note_viewed", note_id="n-2")

        rows = await svc.get_most_used(alice, algorithm="log_count")
        scores = {r.note_id: r.score for r in rows}
        assert math.isclose(scores["n-2"], math.log(6), rel_tol=1e-9)
        assert math.isclose(scores["n-1"], math.log(2), rel_tol=1e-9)

    @pytest.mark.asyncio
    async def test_unique_per_day_algorithm(
        self, activity_repo: PostgresActivityRepo,
        directory_repo: _TestDirectoryRepo,
        alice: _FakeUserContext,
    ) -> None:
        perms = _FakePermissionRepo(viewable_note_ids=["n-1"])
        svc = DefaultActivityStatisticsService(
            activity_repo=activity_repo,
            permission_repo=perms,
            directory_repo=directory_repo,
        )
        # alice: 2 views on n-1 -> 1 unique
        await _insert(activity_repo, action="note_viewed", note_id="n-1", actor_id="alice")
        await _insert(activity_repo, action="note_viewed", note_id="n-1", actor_id="alice")
        # bob: 1 view on n-1 -> 1 unique
        await _insert(activity_repo, action="note_viewed", note_id="n-1", actor_id="bob")

        rows = await svc.get_most_used(
            alice, note_id="n-1", unique_per_day=True,
        )
        assert rows[0].note_id == "n-1"
        assert rows[0].score == 2