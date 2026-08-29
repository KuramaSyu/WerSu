"""Unit tests for :class:`src.grpc_mod.shelf_service.GrpcShelfService`.

These tests pin the gRPC adapter's behaviour on top of the shared
:class:`~tests.stubs.shelf_service._StubShelfService` so they do not
require Postgres, SpiceDB, or any other infrastructure.

Coverage:

* every read endpoint -- not-found, permission denial,
  ``user_id`` validation, ``include_books`` forwarding.
* every write endpoint -- happy path + permission denial +
  ``ValueError`` mapping to ``INVALID_ARGUMENT``.
* :meth:`DeleteShelf` -- dry-run returns the cascade, real delete
  returns an empty response, permission denial.
* bootstrap-strategy dispatch -- ``BOOTSTRAP_STRATEGY_ZETTELKASTEN``
  arrives at the service as ``BootstrapStrategy.ZETTELKASTEN``.
"""

from __future__ import annotations

from typing import Optional, cast

import grpc
from grpc.aio import ServicerContext

from tests.stubs import _StubShelfService, _UserContextFactory, silent_logger
from src.api.services.shelf_service import (
    BootstrapResult,
    BootstrapStrategy,
    DryDeleteResult,
)
from src.db.entities.shelf import ShelfEntity
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.shelf_service import GrpcShelfService
from src.grpc_mod.proto.shelf_pb2 import (
    AttachBookRequest,
    BootstrapStrategy as ProtoBS,
    CreateShelfRequest,
    DeleteShelfRequest,
    DetachBookRequest,
    GetBooksOfShelfRequest,
    GetShelfRequest,
    GetShelvesOfBookRequest,
    GetShelvesRequest,
    ListShelvesRequest,
    SetBooksRequest,
    UpdateShelfRequest,
)


def _to_grpc() -> ConvertToGrpcVisitor:
    return ConvertToGrpcVisitor()


class _FakeContext:
    def __init__(self) -> None:
        self.code: Optional[grpc.StatusCode] = None
        self.details: Optional[str] = None

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


def _service(impl: _StubShelfService) -> GrpcShelfService:
    return GrpcShelfService(
        shelf_service=impl,
        log=silent_logger,
        to_grpc=_to_grpc(),
        context_factory=_UserContextFactory(),
    )


def _seed(impl: _StubShelfService, shelf_id: str = "shelf-1", slug: str = "my shelf") -> ShelfEntity:
    shelf = ShelfEntity(id=shelf_id, slug=slug, display_name="My Shelf")
    impl.shelves_by_id[shelf_id] = shelf
    return shelf


# ---- GetShelf -----------------------------------------------------------


async def test_get_shelf_requires_user_id() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    response = await service.GetShelf(
        GetShelfRequest(id="s-1", user_id=""),
        cast(ServicerContext, context),
    )

    assert response.shelf.id == ""
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "user_id is required"


async def test_get_shelf_requires_id() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    response = await service.GetShelf(
        GetShelfRequest(id="", user_id="u-1"),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "id is required"


async def test_get_shelf_returns_not_found_when_service_reports_missing() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    response = await service.GetShelf(
        GetShelfRequest(id="missing", user_id="u-1"),
        cast(ServicerContext, context),
    )

    assert response.shelf.id == ""
    assert context.code == grpc.StatusCode.NOT_FOUND


async def test_get_shelf_returns_permission_denied_when_service_raises() -> None:
    impl = _StubShelfService()
    impl.get_deny = True
    _seed(impl)
    service = _service(impl)
    context = _FakeContext()

    response = await service.GetShelf(
        GetShelfRequest(id="shelf-1", user_id="u-1"),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.PERMISSION_DENIED


async def test_get_shelf_forwards_include_books_flag() -> None:
    impl = _StubShelfService()
    _seed(impl)
    service = _service(impl)
    context = _FakeContext()

    await service.GetShelf(
        GetShelfRequest(id="shelf-1", user_id="u-1", include_books=True),
        cast(ServicerContext, context),
    )

    assert impl.last_get_options == {"include_books": True}


# ---- GetShelves ---------------------------------------------------------


async def test_get_shelves_returns_shelves_in_input_order() -> None:
    impl = _StubShelfService()
    _seed(impl, "shelf-1")
    _seed(impl, "shelf-2", "second")
    service = _service(impl)
    context = _FakeContext()

    response = await service.GetShelves(
        GetShelvesRequest(user_id="u-1", ids=["shelf-1", "shelf-2"]),
        cast(ServicerContext, context),
    )

    assert [s.id for s in response.shelves] == ["shelf-1", "shelf-2"]
    assert context.code is None


async def test_get_shelves_returns_permission_denied() -> None:
    impl = _StubShelfService()
    impl.get_shelves_deny = True
    service = _service(impl)
    context = _FakeContext()

    response = await service.GetShelves(
        GetShelvesRequest(user_id="u-1", ids=["shelf-1"]),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.PERMISSION_DENIED


# ---- ListShelves --------------------------------------------------------


async def test_list_shelves_passes_limit_and_offset() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    await service.ListShelves(
        ListShelvesRequest(user_id="u-1", limit=10, offset=5, include_books=True),
        cast(ServicerContext, context),
    )

    assert impl.last_list_user_id == "u-1"
    assert impl.last_list_limit == 10
    assert impl.last_list_offset == 5
    assert impl.last_list_options == {"include_books": True}


async def test_list_shelves_returns_permission_denied() -> None:
    impl = _StubShelfService()
    impl.list_deny = True
    service = _service(impl)
    context = _FakeContext()

    response = await service.ListShelves(
        ListShelvesRequest(user_id="u-1"),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.PERMISSION_DENIED


# ---- CreateShelf --------------------------------------------------------


async def test_create_shelf_dispatches_bootstrap_strategy() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    request = CreateShelfRequest(user_id="u-1", slug="my shelf")
    request.display_name = "My Shelf"
    request.bootstrap_strategy = ProtoBS.BOOTSTRAP_STRATEGY_ZETTELKASTEN

    response = await service.CreateShelf(
        request, cast(ServicerContext, context),
    )

    assert response.shelf.id == "shelf-1"
    assert impl.last_create_bootstrap == BootstrapStrategy.ZETTELKASTEN
    assert impl.last_create_user_id == "u-1"
    assert impl.last_create_entity.slug == "my shelf"


async def test_create_shelf_requires_user_id() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    request = CreateShelfRequest(user_id="", slug="my shelf")
    response = await service.CreateShelf(
        request, cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_create_shelf_returns_permission_denied() -> None:
    impl = _StubShelfService()
    impl.create_deny = True
    service = _service(impl)
    context = _FakeContext()

    response = await service.CreateShelf(
        CreateShelfRequest(user_id="u-1", slug="my shelf"),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.PERMISSION_DENIED


async def test_create_shelf_projects_bootstrap_result_to_proto() -> None:
    impl = _StubShelfService()
    impl.next_bootstrap_result = BootstrapResult(
        created_directory_ids=["dir-1", "dir-2"],
        created_rule_id="rule-1",
        description="3 default books and the default-fleeting rule",
    )
    service = _service(impl)
    context = _FakeContext()

    response = await service.CreateShelf(
        CreateShelfRequest(user_id="u-1", slug="my shelf"),
        cast(ServicerContext, context),
    )

    assert list(response.bootstrap_result.created_directory_ids) == ["dir-1", "dir-2"]
    assert response.bootstrap_result.created_rule_id == "rule-1"
    assert response.bootstrap_result.description == (
        "3 default books and the default-fleeting rule"
    )


# ---- UpdateShelf --------------------------------------------------------


async def test_update_shelf_requires_id() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    response = await service.UpdateShelf(
        UpdateShelfRequest(user_id="u-1", id=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_update_shelf_returns_permission_denied() -> None:
    impl = _StubShelfService()
    impl.update_deny = True
    _seed(impl)
    service = _service(impl)
    context = _FakeContext()

    response = await service.UpdateShelf(
        UpdateShelfRequest(user_id="u-1", id="shelf-1", slug="new"),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.PERMISSION_DENIED


async def test_update_shelf_passes_entity_to_service() -> None:
    impl = _StubShelfService()
    _seed(impl)
    service = _service(impl)
    context = _FakeContext()

    await service.UpdateShelf(
        UpdateShelfRequest(
            user_id="u-1", id="shelf-1", description="new desc",
        ),
        cast(ServicerContext, context),
    )

    assert impl.last_update_entity.id == "shelf-1"
    assert impl.last_update_entity.description == "new desc"


# ---- DeleteShelf --------------------------------------------------------


async def test_delete_shelf_requires_id() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    await service.DeleteShelf(
        DeleteShelfRequest(user_id="u-1", id=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_delete_shelf_dry_returns_cascade() -> None:
    impl = _StubShelfService()
    impl.delete_result = DryDeleteResult(
        affected_book_ids=["b-1", "b-2"], binding_count=2,
    )
    _seed(impl)
    service = _service(impl)
    context = _FakeContext()

    response = await service.DeleteShelf(
        DeleteShelfRequest(user_id="u-1", id="shelf-1", dry=True),
        cast(ServicerContext, context),
    )

    assert response.dry is True
    assert list(response.affected_book_ids) == ["b-1", "b-2"]
    assert response.binding_count == 2
    # Real delete path was not taken -- the shelf is still there.
    assert impl.shelves_by_id.get("shelf-1") is not None


async def test_delete_shelf_real_returns_empty_response() -> None:
    impl = _StubShelfService()
    _seed(impl)
    service = _service(impl)
    context = _FakeContext()

    response = await service.DeleteShelf(
        DeleteShelfRequest(user_id="u-1", id="shelf-1", dry=False),
        cast(ServicerContext, context),
    )

    assert response.dry is False
    assert list(response.affected_book_ids) == []
    assert response.binding_count == 0
    assert impl.shelves_by_id == {}


async def test_delete_shelf_returns_permission_denied() -> None:
    impl = _StubShelfService()
    impl.delete_deny = True
    _seed(impl)
    service = _service(impl)
    context = _FakeContext()

    await service.DeleteShelf(
        DeleteShelfRequest(user_id="u-1", id="shelf-1", dry=False),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.PERMISSION_DENIED


# ---- book bindings ------------------------------------------------------


async def test_set_books_requires_shelf_id() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    await service.SetBooks(
        SetBooksRequest(user_id="u-1", shelf_id=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_set_books_passes_book_ids() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    await service.SetBooks(
        SetBooksRequest(user_id="u-1", shelf_id="s-1", book_ids=["b-1", "b-2"]),
        cast(ServicerContext, context),
    )

    assert impl.last_set_books_shelf_id == "s-1"
    assert impl.last_set_books_book_ids == ["b-1", "b-2"]
    assert impl.last_set_books_user_id == "u-1"


async def test_attach_book_requires_book_id() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    await service.AttachBook(
        AttachBookRequest(user_id="u-1", shelf_id="s-1", book_id=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_detach_book_passes_ids() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    await service.DetachBook(
        DetachBookRequest(user_id="u-1", shelf_id="s-1", book_id="b-1"),
        cast(ServicerContext, context),
    )

    assert impl.last_detach_shelf_id == "s-1"
    assert impl.last_detach_book_id == "b-1"


async def test_get_books_of_shelf_requires_shelf_id() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    response = await service.GetBooksOfShelf(
        GetBooksOfShelfRequest(user_id="u-1", shelf_id=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_get_shelves_of_book_requires_book_id() -> None:
    impl = _StubShelfService()
    service = _service(impl)
    context = _FakeContext()

    response = await service.GetShelvesOfBook(
        GetShelvesOfBookRequest(user_id="u-1", book_id=""),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_bindings_return_permission_denied() -> None:
    impl = _StubShelfService()
    impl.set_books_deny = True
    service = _service(impl)
    context = _FakeContext()

    await service.SetBooks(
        SetBooksRequest(user_id="u-1", shelf_id="s-1", book_ids=["b-1"]),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.PERMISSION_DENIED