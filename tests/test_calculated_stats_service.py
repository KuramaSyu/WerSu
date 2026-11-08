"""Unit tests for :class:`CalculatedStatsServiceImpl`.

The service is exercised against the in-memory permission repo and a
small stats-repo double, so the wildcard ``note`` / ``directory``
lookups and the user filter are asserted without a database.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from src.api.other.relationship import (
    PAGE_ROOT_ID,
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    PageRelationEnum,
    Relationship,
    SubjectRef,
)
from src.db.entities.calculated_stats import (
    CalculatedStatsEntity,
    CalculatedStatsFilter,
)
from src.services.calculated_stats_service import CalculatedStatsServiceImpl
from tests.doubles import (
    InMemoryPermissionRepo,
    _DummyUserContext,
    _FakeCalculatedStatsRepo,
)


def _view_relationship(
    object_type: ObjectTypeEnum,
    object_id: str,
    user_id: str,
) -> Relationship:
    relation = (
        NoteRelationEnum.VIEW
        if object_type is ObjectTypeEnum.NOTE
        else DirectoryRelationEnum.VIEW
    )
    return Relationship(
        resource=ObjectRef(object_type, object_id),
        relation=relation,
        subject=SubjectRef(ObjectTypeEnum.USER, user_id),
    )


@pytest.fixture
def permission_repo() -> InMemoryPermissionRepo:
    return InMemoryPermissionRepo()


@pytest.fixture
def stats_repo() -> _FakeCalculatedStatsRepo:
    return _FakeCalculatedStatsRepo()


@pytest.fixture
def service(
    stats_repo: _FakeCalculatedStatsRepo,
    permission_repo: InMemoryPermissionRepo,
) -> CalculatedStatsServiceImpl:
    return CalculatedStatsServiceImpl(
        stats_repo=stats_repo,
        permission_repo=permission_repo,
        get_now=lambda: _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
    )


class TestRecalculate:
    async def test_recalculates_all_users(
        self,
        service: CalculatedStatsServiceImpl,
        stats_repo: _FakeCalculatedStatsRepo,
        permission_repo: InMemoryPermissionRepo,
    ) -> None:
        await permission_repo.insert(
            [
                _view_relationship(ObjectTypeEnum.NOTE, "note-a", "u1"),
                _view_relationship(ObjectTypeEnum.NOTE, "note-b", "u1"),
                _view_relationship(ObjectTypeEnum.DIRECTORY, "dir-a", "u1"),
                _view_relationship(ObjectTypeEnum.DIRECTORY, "dir-b", "u1"),
            ]
        )

        rows = await service.recalculate()

        assert [row.user_id for row in rows] == ["u1", "u2"]
        assert stats_repo.rows["u1"].total_notes == 2
        assert stats_repo.rows["u1"].total_books == 1
        assert stats_repo.rows["u1"].total_chapters == 1
        # No permissions -> zero note / directory ids, still persisted.
        assert stats_repo.rows["u2"].total_notes == 0
        assert stats_repo.rows["u2"].total_books == 0
        assert stats_repo.rows["u2"].total_chapters == 0

    async def test_filter_restricts_to_given_users(
        self,
        service: CalculatedStatsServiceImpl,
        stats_repo: _FakeCalculatedStatsRepo,
    ) -> None:
        await service.recalculate(
            CalculatedStatsFilter(user_ids=["u2", "u2", ""])
        )

        assert list(stats_repo.rows) == ["u2"]

    async def test_empty_filter_performs_no_work(
        self,
        service: CalculatedStatsServiceImpl,
        stats_repo: _FakeCalculatedStatsRepo,
    ) -> None:
        rows = await service.recalculate(CalculatedStatsFilter(user_ids=[]))

        assert rows == []
        assert stats_repo.rows == {}


class TestGetStats:
    async def test_actor_can_read_own_stats(
        self,
        service: CalculatedStatsServiceImpl,
        stats_repo: _FakeCalculatedStatsRepo,
    ) -> None:
        stats_repo.rows["u1"] = CalculatedStatsEntity(user_id="u1")

        got = await service.get_stats(_DummyUserContext(user_id="u1"))

        assert got is stats_repo.rows["u1"]

    async def test_other_user_requires_page_admin(
        self,
        service: CalculatedStatsServiceImpl,
        stats_repo: _FakeCalculatedStatsRepo,
    ) -> None:
        stats_repo.rows["u2"] = CalculatedStatsEntity(user_id="u2")

        with pytest.raises(PermissionError):
            await service.get_stats(
                _DummyUserContext(user_id="u1"), user_id="u2"
            )

    async def test_page_admin_can_read_other_user_stats(
        self,
        service: CalculatedStatsServiceImpl,
        stats_repo: _FakeCalculatedStatsRepo,
        permission_repo: InMemoryPermissionRepo,
    ) -> None:
        stats_repo.rows["u2"] = CalculatedStatsEntity(user_id="u2")
        await permission_repo.insert(
            [
                Relationship(
                    resource=ObjectRef(
                        ObjectTypeEnum.PAGE, PAGE_ROOT_ID
                    ),
                    relation=PageRelationEnum.ADMIN,
                    subject=SubjectRef(ObjectTypeEnum.USER, "admin"),
                )
            ]
        )

        got = await service.get_stats(
            _DummyUserContext(user_id="admin"), user_id="u2"
        )

        assert got is stats_repo.rows["u2"]
