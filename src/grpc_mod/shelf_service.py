"""gRPC adapter for :class:`src.api.services.shelf_service.ShelfServiceABC`.

Implements the ``ShelfService`` from ``src/grpc_mod/proto/shelf.proto``.
Every method requires ``request.user_id`` so the service can build
a :class:`UserContextABC` for permission checks; permission
enforcement itself lives in the service layer (every read is
gated on ``shelf#view``, every write on ``shelf#write``, every
delete on ``shelf#delete``).

The adapter is intentionally thin: it converts proto messages to
domain entities via :mod:`src.grpc_mod.converter.shelf_converter`,
delegates to the service layer, and converts the result back via
the injected :class:`ConvertToGrpcVisitor`.  Error handling mirrors
the existing gRPC adapters -- :exc:`ShelfPermissionError` and
:exc:`ValueError` are mapped to ``PERMISSION_DENIED`` and
``INVALID_ARGUMENT`` respectively; everything else is logged and
returned as ``INTERNAL``.
"""

from __future__ import annotations

import traceback

import grpc
from google.protobuf.empty_pb2 import Empty
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.services.shelf_service import (
    BootstrapResult,
    ShelfPermissionError,
    ShelfServiceABC,
    ShelfServiceError,
)
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.converter.shelf_converter import (
    grpc_bootstrap_strategy_from_proto,
    grpc_create_shelf_to_entity,
    grpc_update_shelf_to_entity,
)
from src.grpc_mod.proto.shelf_pb2 import (
    AttachBookRequest,
    BooksResponse,
    CreateShelfRequest,
    CreateShelfResponse,
    DeleteShelfRequest,
    DeleteShelfResponse,
    DetachBookRequest,
    GetBooksOfShelfRequest,
    GetShelfRequest,
    GetShelvesOfBookRequest,
    GetShelvesRequest,
    ListShelvesRequest,
    SetBooksRequest,
    Shelf as GrpcShelf,
    ShelfIdsResponse,
    ShelfResponse,
    ShelvesResponse,
    BootstrapResult as GrpcBootstrapResult,
)
from src.grpc_mod.proto.shelf_pb2_grpc import ShelfServiceServicer


# ---------------------------------------------------------------------------
# proto <-> result-object helpers
# ---------------------------------------------------------------------------


def _bootstrap_result_to_proto(result: BootstrapResult) -> GrpcBootstrapResult:
    """Project a domain :class:`BootstrapResult` onto the proto message."""
    return GrpcBootstrapResult(
        created_directory_ids=list(result.created_directory_ids),
        created_rule_id=str(result.created_rule_id or ""),
        description=str(result.description or ""),
    )


def _shelf_to_proto(entity, to_grpc: ConvertToGrpcVisitor) -> GrpcShelf:
    """Convert one :class:`ShelfEntity` via the injected visitor."""
    return entity.convert(to_grpc)


# ---------------------------------------------------------------------------
# adapter
# ---------------------------------------------------------------------------


class GrpcShelfService(ShelfServiceServicer):
    """gRPC adapter for :class:`ShelfServiceABC`."""

    def __init__(
        self,
        shelf_service: ShelfServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._shelf_service = shelf_service
        self._to_grpc = to_grpc
        self._context = context_factory
        self.log = log(__name__, self)

    # ---- read ------------------------------------------------------------

    @log_service_call()
    async def GetShelf(
        self,
        request: GetShelfRequest,
        context: ServicerContext,
    ) -> ShelfResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "id is required",
                    response=ShelfResponse(),
                )
            shelf = await self._shelf_service.get_shelf(
                request.id,
                await self._context.create(request.user_id),
                options={"include_books": request.include_books},
            )
            if shelf is None:
                return self._fail(
                    context, grpc.StatusCode.NOT_FOUND, "shelf not found",
                    response=ShelfResponse(),
                )
            return ShelfResponse(shelf=_shelf_to_proto(shelf, self._to_grpc))
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=ShelfResponse(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=ShelfResponse(),
            )
        except Exception:
            self.log.error(f"GetShelf failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=ShelfResponse(),
            )

    @log_service_call()
    async def GetShelves(
        self,
        request: GetShelvesRequest,
        context: ServicerContext,
    ) -> ShelvesResponse:
        try:
            self._require_user_id(request.user_id)
            shelves = await self._shelf_service.get_shelves(
                list(request.ids),
                await self._context.create(request.user_id),
                options={"include_books": request.include_books},
            )
            return ShelvesResponse(
                shelves=[_shelf_to_proto(s, self._to_grpc) for s in shelves],
            )
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=ShelvesResponse(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=ShelvesResponse(),
            )
        except Exception:
            self.log.error(f"GetShelves failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=ShelvesResponse(),
            )

    @log_service_call()
    async def ListShelves(
        self,
        request: ListShelvesRequest,
        context: ServicerContext,
    ) -> ShelvesResponse:
        try:
            self._require_user_id(request.user_id)
            kwargs = {
                "options": {"include_books": request.include_books},
            }
            if request.HasField("limit"):
                kwargs["limit"] = request.limit
            if request.HasField("offset"):
                kwargs["offset"] = request.offset
            shelves = await self._shelf_service.list_shelves(
                await self._context.create(request.user_id),
                **kwargs,
            )
            return ShelvesResponse(
                shelves=[_shelf_to_proto(s, self._to_grpc) for s in shelves],
            )
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=ShelvesResponse(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=ShelvesResponse(),
            )
        except Exception:
            self.log.error(f"ListShelves failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=ShelvesResponse(),
            )

    # ---- create / update ------------------------------------------------

    @log_service_call()
    async def CreateShelf(
        self,
        request: CreateShelfRequest,
        context: ServicerContext,
    ) -> CreateShelfResponse:
        try:
            self._require_user_id(request.user_id)
            entity = grpc_create_shelf_to_entity(request)
            bootstrap = grpc_bootstrap_strategy_from_proto(request.bootstrap_strategy)
            shelf, bootstrap_result = await self._shelf_service.create_shelf(
                entity,
                await self._context.create(request.user_id),
                bootstrap=bootstrap,
            )
            return CreateShelfResponse(
                shelf=_shelf_to_proto(shelf, self._to_grpc),
                bootstrap_result=_bootstrap_result_to_proto(bootstrap_result),
            )
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=CreateShelfResponse(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=CreateShelfResponse(),
            )
        except Exception:
            self.log.error(f"CreateShelf failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=CreateShelfResponse(),
            )

    @log_service_call()
    async def UpdateShelf(
        self,
        request: UpdateShelfRequest,
        context: ServicerContext,
    ) -> ShelfResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "id is required",
                    response=ShelfResponse(),
                )
            entity = grpc_update_shelf_to_entity(request)
            shelf = await self._shelf_service.update_shelf(
                entity, await self._context.create(request.user_id),
            )
            return ShelfResponse(shelf=_shelf_to_proto(shelf, self._to_grpc))
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=ShelfResponse(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=ShelfResponse(),
            )
        except Exception:
            self.log.error(f"UpdateShelf failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=ShelfResponse(),
            )

    # ---- delete ---------------------------------------------------------

    @log_service_call()
    async def DeleteShelf(
        self,
        request: DeleteShelfRequest,
        context: ServicerContext,
    ) -> DeleteShelfResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "id is required",
                    response=DeleteShelfResponse(),
                )
            result = await self._shelf_service.delete_shelf(
                request.id,
                await self._context.create(request.user_id),
                dry=request.dry,
            )
            if not request.dry:
                # Real delete -- return an empty ``DeleteShelfResponse``
                # so the proto field set stays consistent for
                # clients that don't branch on ``dry``.
                return DeleteShelfResponse(
                    dry=False,
                    affected_book_ids=[],
                    binding_count=0,
                )
            affected = list(result.affected_book_ids) if result else []
            return DeleteShelfResponse(
                dry=True,
                affected_book_ids=affected,
                binding_count=len(affected),
            )
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=DeleteShelfResponse(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=DeleteShelfResponse(),
            )
        except Exception:
            self.log.error(f"DeleteShelf failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=DeleteShelfResponse(),
            )

    # ---- book bindings --------------------------------------------------

    @log_service_call()
    async def SetBooks(
        self,
        request: SetBooksRequest,
        context: ServicerContext,
    ) -> Empty:
        try:
            self._require_user_id(request.user_id)
            if not request.shelf_id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "shelf_id is required",
                    response=Empty(),
                )
            await self._shelf_service.set_books(
                request.shelf_id,
                list(request.book_ids),
                await self._context.create(request.user_id),
            )
            return Empty()
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=Empty(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=Empty(),
            )
        except Exception:
            self.log.error(f"SetBooks failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=Empty(),
            )

    @log_service_call()
    async def AttachBook(
        self,
        request: AttachBookRequest,
        context: ServicerContext,
    ) -> Empty:
        try:
            self._require_user_id(request.user_id)
            if not request.shelf_id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "shelf_id is required",
                    response=Empty(),
                )
            if not request.book_id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "book_id is required",
                    response=Empty(),
                )
            await self._shelf_service.attach_book(
                request.shelf_id,
                request.book_id,
                await self._context.create(request.user_id),
            )
            return Empty()
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=Empty(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=Empty(),
            )
        except Exception:
            self.log.error(f"AttachBook failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=Empty(),
            )

    @log_service_call()
    async def DetachBook(
        self,
        request: DetachBookRequest,
        context: ServicerContext,
    ) -> Empty:
        try:
            self._require_user_id(request.user_id)
            if not request.shelf_id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "shelf_id is required",
                    response=Empty(),
                )
            if not request.book_id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "book_id is required",
                    response=Empty(),
                )
            await self._shelf_service.detach_book(
                request.shelf_id,
                request.book_id,
                await self._context.create(request.user_id),
            )
            return Empty()
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=Empty(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=Empty(),
            )
        except Exception:
            self.log.error(f"DetachBook failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=Empty(),
            )

    @log_service_call()
    async def GetBooksOfShelf(
        self,
        request: GetBooksOfShelfRequest,
        context: ServicerContext,
    ) -> BooksResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.shelf_id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "shelf_id is required",
                    response=BooksResponse(),
                )
            book_ids = await self._shelf_service.get_books_of_shelf(
                request.shelf_id,
                await self._context.create(request.user_id),
            )
            return BooksResponse(book_ids=list(book_ids))
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=BooksResponse(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=BooksResponse(),
            )
        except Exception:
            self.log.error(f"GetBooksOfShelf failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=BooksResponse(),
            )

    @log_service_call()
    async def GetShelvesOfBook(
        self,
        request: GetShelvesOfBookRequest,
        context: ServicerContext,
    ) -> ShelfIdsResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.book_id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "book_id is required",
                    response=ShelfIdsResponse(),
                )
            shelf_ids = await self._shelf_service.get_shelves_of_book(
                request.book_id,
                await self._context.create(request.user_id),
            )
            return ShelfIdsResponse(shelf_ids=list(shelf_ids))
        except ShelfPermissionError as exc:
            return self._fail(
                context, grpc.StatusCode.PERMISSION_DENIED, str(exc),
                response=ShelfIdsResponse(),
            )
        except (ShelfServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=ShelfIdsResponse(),
            )
        except Exception:
            self.log.error(f"GetShelvesOfBook failed: {traceback.format_exc()}\nrequest: {request}")
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=ShelfIdsResponse(),
            )

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _require_user_id(user_id: str) -> None:
        """Raise ``ValueError`` when ``user_id`` is empty.

        We raise instead of returning a ``ServicerContext``
        failure so the caller stays free to map the error.
        The actual ``set_code`` happens in the handler.
        """
        if not user_id:
            raise ValueError("user_id is required")

    @staticmethod
    def _fail(
        context: ServicerContext,
        code: grpc.StatusCode,
        message: str,
        response: object = None,
    ):
        """Set the gRPC error code/details on ``context``.

        ``response`` lets callers provide a typed default
        (e.g. ``ShelfResponse()``, ``ShelvesResponse()``) so the
        gRPC framework has the right message shape to send.
        Defaults to ``None`` -- callers that always pass a
        response explicitly are unaffected.
        """
        context.set_code(code)
        context.set_details(message)
        return response


__all__ = ["GrpcShelfService"]