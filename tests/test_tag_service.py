"""Tests for :class:`src.services.tag_service.TagServiceImpl`.

The real service is driven against the in-memory
:class:`tests._fixtures_pkg.fakes._FakeTagRepo` so no Postgres is
required.  The fake implements the full
:class:`~src.api.repos.tag_repo.TagRepoABC` surface, which lets us
assert the service's validation + delegation contract end to end.
"""

from __future__ import annotations

import pytest

from src.db.entities.note.tag import TagEntity
from src.services.tag_service import TagServiceImpl
from tests._fixtures_pkg.fakes import _FakeTagRepo
from tests.stubs.user_context import _UserContext


_ACTOR = _UserContext("actor")


def _service() -> tuple[TagServiceImpl, _FakeTagRepo]:
    """Build a real :class:`TagServiceImpl` over an in-memory repo."""
    repo = _FakeTagRepo()
    return TagServiceImpl(tag_repo=repo), repo


# ---- create ---------------------------------------------------------------


async def test_create_tag_persists_and_returns_entity() -> None:
    service, repo = _service()

    tag = await service.create_tag("python", "Python", _ACTOR)

    assert tag.id == "tag-1"
    assert tag.slug == "python"
    assert tag.display_name == "Python"
    # The entity the service handed back is the persisted one.
    assert repo.tags_by_id["tag-1"] == tag


async def test_create_tag_rejects_empty_slug() -> None:
    service, _ = _service()
    with pytest.raises(ValueError, match="slug is required"):
        await service.create_tag("", "Python", _ACTOR)


async def test_create_tag_rejects_empty_display_name() -> None:
    service, _ = _service()
    with pytest.raises(ValueError, match="display_name is required"):
        await service.create_tag("python", "", _ACTOR)


# ---- read -----------------------------------------------------------------


async def test_get_tag_returns_entity_by_id() -> None:
    service, _ = _service()
    created = await service.create_tag("python", "Python", _ACTOR)

    fetched = await service.get_tag(str(created.id), _ACTOR)

    assert fetched == created


async def test_get_tag_returns_none_when_missing() -> None:
    service, _ = _service()
    assert await service.get_tag("missing", _ACTOR) is None


async def test_get_tag_rejects_empty_id() -> None:
    service, _ = _service()
    with pytest.raises(ValueError, match="tag_id is required"):
        await service.get_tag("", _ACTOR)


async def test_list_tags_is_empty_for_fresh_taxonomy() -> None:
    service, _ = _service()
    assert await service.list_tags(_ACTOR) == []


async def test_list_tags_sorts_by_slug() -> None:
    service, _ = _service()
    await service.create_tag("zeta", "Zeta", _ACTOR)
    await service.create_tag("alpha", "Alpha", _ACTOR)
    await service.create_tag("mid", "Mid", _ACTOR)

    tags = await service.list_tags(_ACTOR)

    assert [t.slug for t in tags] == ["alpha", "mid", "zeta"]


# ---- update ---------------------------------------------------------------


async def test_update_tag_changes_display_name_only() -> None:
    service, _ = _service()
    created = await service.create_tag("python", "Python", _ACTOR)

    updated = await service.update_tag(
        str(created.id), _ACTOR, display_name="Python 3",
    )

    assert updated is not None
    assert updated.slug == "python"
    assert updated.display_name == "Python 3"


async def test_update_tag_changes_slug_only() -> None:
    service, _ = _service()
    created = await service.create_tag("python", "Python", _ACTOR)

    updated = await service.update_tag(
        str(created.id), _ACTOR, slug="py",
    )

    assert updated is not None
    assert updated.slug == "py"
    assert updated.display_name == "Python"


async def test_update_tag_returns_none_when_missing() -> None:
    service, _ = _service()
    assert await service.update_tag("missing", _ACTOR, slug="x") is None


async def test_update_tag_without_fields_returns_existing_row() -> None:
    service, _ = _service()
    created = await service.create_tag("python", "Python", _ACTOR)

    updated = await service.update_tag(str(created.id), _ACTOR)

    assert updated is not None
    assert updated.slug == "python"
    assert updated.display_name == "Python"


async def test_update_tag_rejects_empty_id() -> None:
    service, _ = _service()
    with pytest.raises(ValueError, match="tag_id is required"):
        await service.update_tag("", _ACTOR, slug="x")


@pytest.mark.parametrize(
    "kwargs",
    [{"slug": ""}, {"display_name": ""}],
)
async def test_update_tag_rejects_blank_provided_field(kwargs: dict) -> None:
    service, _ = _service()
    created = await service.create_tag("python", "Python", _ACTOR)
    with pytest.raises(ValueError):
        await service.update_tag(str(created.id), _ACTOR, **kwargs)


# ---- delete ---------------------------------------------------------------


async def test_delete_tag_returns_true_when_removed() -> None:
    service, repo = _service()
    created = await service.create_tag("python", "Python", _ACTOR)

    removed = await service.delete_tag(str(created.id), _ACTOR)

    assert removed is True
    assert repo.tags_by_id == {}


async def test_delete_tag_returns_false_when_missing() -> None:
    service, _ = _service()
    assert await service.delete_tag("missing", _ACTOR) is False


async def test_delete_tag_rejects_empty_id() -> None:
    service, _ = _service()
    with pytest.raises(ValueError, match="tag_id is required"):
        await service.delete_tag("", _ACTOR)


# ---- structural -----------------------------------------------------------


async def test_service_returns_tag_entities() -> None:
    """The service never leaks raw repo rows -- only ``TagEntity``."""
    service, _ = _service()
    created = await service.create_tag("python", "Python", _ACTOR)
    assert isinstance(created, TagEntity)
