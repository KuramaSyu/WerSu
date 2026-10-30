"""Integration tests for the tag service against real Postgres.

Uses the real :class:`src.services.tag_service.TagServiceImpl` on top
of the real :class:`src.db.repos.tag.postgres.PostgresTagRepo`
(provided by the ``tag_repo`` fixture) so the SQL, the taxonomy
constraints and the service validation are exercised together.
"""

from __future__ import annotations

import pytest

from src.api.repos.tag_repo import TagRepoABC
from src.services.tag_service import TagServiceImpl
from tests.stubs.user_context import _UserContext as UserContext

pytestmark = pytest.mark.integration


async def test_tag_service_crud_round_trip(tag_repo: TagRepoABC) -> None:
    service = TagServiceImpl(tag_repo=tag_repo)
    actor = UserContext("actor")

    created = await service.create_tag("integration-python", "Python", actor)
    assert created.id
    assert created.slug == "integration-python"
    assert created.display_name == "Python"

    fetched = await service.get_tag(str(created.id), actor)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.slug == "integration-python"

    listed = await service.list_tags(actor)
    assert [t.slug for t in listed] == ["integration-python"]

    updated = await service.update_tag(
        str(created.id), actor, display_name="Python 3",
    )
    assert updated is not None
    assert updated.slug == "integration-python"
    assert updated.display_name == "Python 3"

    removed = await service.delete_tag(str(created.id), actor)
    assert removed is True
    assert await service.get_tag(str(created.id), actor) is None
    assert await service.list_tags(actor) == []


async def test_tag_service_delete_missing_returns_false(
    tag_repo: TagRepoABC,
) -> None:
    service = TagServiceImpl(tag_repo=tag_repo)
    assert await service.delete_tag("does-not-exist", UserContext("actor")) is False
