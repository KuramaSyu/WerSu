"""Tests for :class:`src.db.repos.permissions.postgres_role_repo.PostgresRoleRepo`.

The repo owns Postgres role metadata only.  SpiceDB grants and
membership tuples are written by ``RoleServiceImpl``.
"""

from __future__ import annotations

import pytest

from src.db.entities.user.role import RoleEntity, RoleFilter
from src.db.repos.permissions.postgres_role_repo import PostgresRoleRepo
from tests.doubles import RecordingTable, _DummyUserContext


def _repo(table: RecordingTable) -> PostgresRoleRepo:
    return PostgresRoleRepo(table=table)


@pytest.fixture
def table() -> RecordingTable:
    return RecordingTable(name="roles")


async def test_create_role_inserts_metadata_only(table: RecordingTable) -> None:
    """The repo does not require or touch a permission repo."""
    repo = _repo(table)
    created = await repo.create_role(
        RoleEntity(name="engineering"),
        _DummyUserContext(user_id="alice"),
    )

    assert created.name == "engineering"
    assert created.id is not None
    assert len(table.calls_of("insert")) == 1


async def test_create_role_does_not_require_creator_id(table: RecordingTable) -> None:
    """Creator grants moved to the service, so the repo needs no user id."""
    repo = _repo(table)
    created = await repo.create_role(
        RoleEntity(name="engineering"),
        _DummyUserContext(user_id=""),
    )

    assert created.name == "engineering"


async def test_get_roles_only_handles_name_filter(table: RecordingTable) -> None:
    """``member_id`` is resolved by the service through SpiceDB."""
    repo = _repo(table)
    table.fetch_returns = []

    roles = await repo.get_roles(
        RoleFilter(member_id="alice"),
        _DummyUserContext(user_id="alice"),
    )

    assert roles == []
    query = table.calls_of("fetch")[-1].kwargs["sql"]
    assert "member_id" not in query
