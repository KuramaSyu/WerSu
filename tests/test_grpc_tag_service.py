"""Tests for :class:`src.grpc_mod.tag_service.GrpcTagService`.

These tests wire the **real** :class:`GrpcTagService` to the **real**
:class:`src.services.tag_service.TagServiceImpl`, backed by the
in-memory :class:`tests.doubles.fakespys.tag_repo._FakeSpyTagRepo`.  That
exercises the whole stack (proto -> adapter -> service -> repo) while
still running without Postgres, SpiceDB or any other infrastructure.
"""

from __future__ import annotations

from typing import Optional, cast

import asyncpg
import grpc
from grpc.aio import ServicerContext

from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.tag_pb2 import (
    CreateTagRequest,
    DeleteTagRequest,
    GetTagRequest,
    ListTagsRequest,
    UpdateTagRequest,
)
from src.grpc_mod.tag_service import GrpcTagService
from src.services.tag_service import TagServiceImpl
from tests.doubles import _FakeSpyTagRepo
from tests.doubles import _StubUserContextFactory, silent_logger

from tests.doubles.dummies.fake_context import _DummyContext
from tests.doubles.mocks.unique_violation_tag_repo import _MockUniqueViolationTagRepo






def _service(
    repo: Optional[_FakeSpyTagRepo] = None,
) -> tuple[GrpcTagService, _FakeSpyTagRepo]:
    """Build the real gRPC adapter over the real service + fake repo."""
    real_repo = repo if repo is not None else _FakeSpyTagRepo()
    impl = TagServiceImpl(tag_repo=real_repo, logging_provider=silent_logger)
    service = GrpcTagService(
        tag_service=impl,
        log=silent_logger,
        to_grpc=ConvertToGrpcVisitor(),
        context_factory=_StubUserContextFactory(),
    )
    return service, real_repo


async def _create(
    service: GrpcTagService,
    slug: str = "python",
    display_name: str = "Python",
) -> None:
    """Seed one tag through the CreateTag RPC."""
    await service.CreateTag(
        CreateTagRequest(user_id="u-1", slug=slug, display_name=display_name),
        cast(ServicerContext, _DummyContext()),
    )


# ---- CreateTag ------------------------------------------------------------


async def test_create_tag_returns_proto() -> None:
    service, repo = _service()
    context = _DummyContext()

    response = await service.CreateTag(
        CreateTagRequest(user_id="u-1", slug="python", display_name="Python"),
        cast(ServicerContext, context),
    )

    assert response.tag.id == "tag-1"
    assert response.tag.slug == "python"
    assert response.tag.display_name == "Python"
    assert context.code is None
    assert "tag-1" in repo.tags_by_id


async def test_create_tag_requires_user_id() -> None:
    service, _ = _service()
    context = _DummyContext()

    response = await service.CreateTag(
        CreateTagRequest(user_id="", slug="python", display_name="Python"),
        cast(ServicerContext, context),
    )

    assert response.tag.id == ""
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "user_id is required"


async def test_create_tag_requires_slug() -> None:
    service, _ = _service()
    context = _DummyContext()

    await service.CreateTag(
        CreateTagRequest(user_id="u-1", slug="", display_name="Python"),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "slug is required"


async def test_create_tag_requires_display_name() -> None:
    service, _ = _service()
    context = _DummyContext()

    await service.CreateTag(
        CreateTagRequest(user_id="u-1", slug="python", display_name=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "display_name is required"


async def test_create_tag_duplicate_slug_maps_to_already_exists() -> None:
    service, _ = _service(repo=_MockUniqueViolationTagRepo())
    context = _DummyContext()

    await service.CreateTag(
        CreateTagRequest(user_id="u-1", slug="python", display_name="Python"),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.ALREADY_EXISTS


# ---- GetTag ---------------------------------------------------------------


async def test_get_tag_returns_seeded_tag() -> None:
    service, _ = _service()
    await _create(service)
    context = _DummyContext()

    response = await service.GetTag(
        GetTagRequest(user_id="u-1", id="tag-1"),
        cast(ServicerContext, context),
    )

    assert response.tag.id == "tag-1"
    assert response.tag.slug == "python"
    assert context.code is None


async def test_get_tag_returns_not_found() -> None:
    service, _ = _service()
    context = _DummyContext()

    response = await service.GetTag(
        GetTagRequest(user_id="u-1", id="missing"),
        cast(ServicerContext, context),
    )

    assert response.tag.id == ""
    assert context.code == grpc.StatusCode.NOT_FOUND


async def test_get_tag_requires_id() -> None:
    service, _ = _service()
    context = _DummyContext()

    await service.GetTag(
        GetTagRequest(user_id="u-1", id=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "id is required"


async def test_get_tag_requires_user_id() -> None:
    service, _ = _service()
    context = _DummyContext()

    await service.GetTag(
        GetTagRequest(user_id="", id="tag-1"),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


# ---- ListTags -------------------------------------------------------------


async def test_list_tags_returns_every_tag_sorted_by_slug() -> None:
    service, _ = _service()
    await _create(service, slug="zeta", display_name="Zeta")
    await _create(service, slug="alpha", display_name="Alpha")
    context = _DummyContext()

    response = await service.ListTags(
        ListTagsRequest(user_id="u-1"),
        cast(ServicerContext, context),
    )

    assert [t.slug for t in response.tags] == ["alpha", "zeta"]
    assert context.code is None


async def test_list_tags_returns_empty_when_taxonomy_is_empty() -> None:
    service, _ = _service()
    context = _DummyContext()

    response = await service.ListTags(
        ListTagsRequest(user_id="u-1"),
        cast(ServicerContext, context),
    )

    assert list(response.tags) == []
    assert context.code is None


async def test_list_tags_requires_user_id() -> None:
    service, _ = _service()
    context = _DummyContext()

    await service.ListTags(
        ListTagsRequest(user_id=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


# ---- UpdateTag ------------------------------------------------------------


async def test_update_tag_changes_only_provided_field() -> None:
    service, repo = _service()
    await _create(service)
    context = _DummyContext()

    request = UpdateTagRequest(user_id="u-1", id="tag-1")
    request.display_name = "Python 3"
    response = await service.UpdateTag(
        request, cast(ServicerContext, context),
    )

    assert response.tag.slug == "python"
    assert response.tag.display_name == "Python 3"
    assert repo.tags_by_id["tag-1"].slug == "python"
    assert context.code is None


async def test_update_tag_returns_not_found() -> None:
    service, _ = _service()
    context = _DummyContext()

    request = UpdateTagRequest(user_id="u-1", id="missing")
    request.slug = "new"
    response = await service.UpdateTag(
        request, cast(ServicerContext, context),
    )

    assert response.tag.id == ""
    assert context.code == grpc.StatusCode.NOT_FOUND


async def test_update_tag_without_fields_returns_existing() -> None:
    service, _ = _service()
    await _create(service)
    context = _DummyContext()

    response = await service.UpdateTag(
        UpdateTagRequest(user_id="u-1", id="tag-1"),
        cast(ServicerContext, context),
    )

    assert response.tag.slug == "python"
    assert context.code is None


async def test_update_tag_requires_id() -> None:
    service, _ = _service()
    context = _DummyContext()

    await service.UpdateTag(
        UpdateTagRequest(user_id="u-1", id=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "id is required"


async def test_update_tag_duplicate_slug_maps_to_already_exists() -> None:
    service, _ = _service(repo=_MockUniqueViolationTagRepo())
    context = _DummyContext()

    request = UpdateTagRequest(user_id="u-1", id="tag-1")
    request.slug = "python"
    await service.UpdateTag(request, cast(ServicerContext, context))

    assert context.code == grpc.StatusCode.ALREADY_EXISTS


async def test_update_tag_requires_user_id() -> None:
    service, _ = _service()
    context = _DummyContext()

    await service.UpdateTag(
        UpdateTagRequest(user_id="", id="tag-1"),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


# ---- DeleteTag ------------------------------------------------------------


async def test_delete_tag_returns_deleted_true() -> None:
    service, repo = _service()
    await _create(service)
    context = _DummyContext()

    response = await service.DeleteTag(
        DeleteTagRequest(user_id="u-1", id="tag-1"),
        cast(ServicerContext, context),
    )

    assert response.deleted is True
    assert repo.tags_by_id == {}
    assert context.code is None


async def test_delete_tag_returns_deleted_false_when_missing() -> None:
    service, _ = _service()
    context = _DummyContext()

    response = await service.DeleteTag(
        DeleteTagRequest(user_id="u-1", id="missing"),
        cast(ServicerContext, context),
    )

    assert response.deleted is False
    assert context.code is None


async def test_delete_tag_requires_id() -> None:
    service, _ = _service()
    context = _DummyContext()

    await service.DeleteTag(
        DeleteTagRequest(user_id="u-1", id=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "id is required"


async def test_delete_tag_requires_user_id() -> None:
    service, _ = _service()
    context = _DummyContext()

    await service.DeleteTag(
        DeleteTagRequest(user_id="", id="tag-1"),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
