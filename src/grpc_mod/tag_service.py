"""gRPC adapter for :class:`src.api.services.tag_service.TagServiceABC`.

Implements the ``TagService`` from ``src/grpc_mod/proto/tag.proto``.
Every method requires ``request.user_id`` so the adapter can build a
:class:`UserContextABC` for the service layer.  Tags carry no SpiceDB
relations, so the service itself performs no permission gating; the
``user_id`` is required for symmetry with the other services.

The adapter is intentionally thin: it converts proto messages to the
service's primitive arguments, delegates, and converts the result back
via :class:`ConvertToGrpcVisitor`.  Error handling mirrors the
existing adapters -- :exc:`ValueError` maps to ``INVALID_ARGUMENT``,
a duplicate slug (``asyncpg.UniqueViolationError``) to
``ALREADY_EXISTS``, a missing row to ``NOT_FOUND``; everything else is
logged and returned as ``INTERNAL``.
"""

from __future__ import annotations

import traceback

import asyncpg
import grpc
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.services.tag_service import TagServiceABC, TagServiceError
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.converter.tag_converter import grpc_update_tag_fields
from src.grpc_mod.proto.tag_pb2 import (
    CreateTagRequest,
    DeleteTagRequest,
    DeleteTagResponse,
    GetTagRequest,
    ListTagsRequest,
    TagResponse,
    TagsResponse,
    UpdateTagRequest,
)
from src.grpc_mod.proto.tag_pb2_grpc import TagServiceServicer


class GrpcTagService(TagServiceServicer):
    """gRPC adapter for :class:`TagServiceABC`."""

    def __init__(
        self,
        tag_service: TagServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._tag_service = tag_service
        self._to_grpc = to_grpc
        self._context = context_factory
        self.log = log(__name__, self)

    # ---- create ---------------------------------------------------------

    @log_service_call()
    async def CreateTag(
        self,
        request: CreateTagRequest,
        context: ServicerContext,
    ) -> TagResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.slug:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT,
                    "slug is required", response=TagResponse(),
                )
            if not request.display_name:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT,
                    "display_name is required", response=TagResponse(),
                )
            tag = await self._tag_service.create_tag(
                request.slug,
                request.display_name,
                await self._context.create(request.user_id),
            )
            return TagResponse(tag=self._to_grpc.visit_tag(tag))
        except asyncpg.UniqueViolationError:
            return self._fail(
                context, grpc.StatusCode.ALREADY_EXISTS,
                f"a tag with slug {request.slug!r} already exists",
                response=TagResponse(),
            )
        except (TagServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=TagResponse(),
            )
        except Exception:
            self.log.error(
                f"CreateTag failed: {traceback.format_exc()}\nrequest: {request}"
            )
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=TagResponse(),
            )

    # ---- read -----------------------------------------------------------

    @log_service_call()
    async def GetTag(
        self,
        request: GetTagRequest,
        context: ServicerContext,
    ) -> TagResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT,
                    "id is required", response=TagResponse(),
                )
            tag = await self._tag_service.get_tag(
                request.id,
                await self._context.create(request.user_id),
            )
            if tag is None:
                return self._fail(
                    context, grpc.StatusCode.NOT_FOUND, "tag not found",
                    response=TagResponse(),
                )
            return TagResponse(tag=self._to_grpc.visit_tag(tag))
        except (TagServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=TagResponse(),
            )
        except Exception:
            self.log.error(
                f"GetTag failed: {traceback.format_exc()}\nrequest: {request}"
            )
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=TagResponse(),
            )

    @log_service_call()
    async def ListTags(
        self,
        request: ListTagsRequest,
        context: ServicerContext,
    ) -> TagsResponse:
        try:
            self._require_user_id(request.user_id)
            tags = await self._tag_service.list_tags(
                await self._context.create(request.user_id),
            )
            return TagsResponse(
                tags=[self._to_grpc.visit_tag(t) for t in tags],
            )
        except (TagServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=TagsResponse(),
            )
        except Exception:
            self.log.error(
                f"ListTags failed: {traceback.format_exc()}\nrequest: {request}"
            )
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=TagsResponse(),
            )

    # ---- update ---------------------------------------------------------

    @log_service_call()
    async def UpdateTag(
        self,
        request: UpdateTagRequest,
        context: ServicerContext,
    ) -> TagResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT,
                    "id is required", response=TagResponse(),
                )
            fields = grpc_update_tag_fields(request)
            tag = await self._tag_service.update_tag(
                request.id,
                await self._context.create(request.user_id),
                **fields,
            )
            if tag is None:
                return self._fail(
                    context, grpc.StatusCode.NOT_FOUND, "tag not found",
                    response=TagResponse(),
                )
            return TagResponse(tag=self._to_grpc.visit_tag(tag))
        except asyncpg.UniqueViolationError:
            return self._fail(
                context, grpc.StatusCode.ALREADY_EXISTS,
                "a tag with that slug already exists",
                response=TagResponse(),
            )
        except (TagServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=TagResponse(),
            )
        except Exception:
            self.log.error(
                f"UpdateTag failed: {traceback.format_exc()}\nrequest: {request}"
            )
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=TagResponse(),
            )

    # ---- delete ---------------------------------------------------------

    @log_service_call()
    async def DeleteTag(
        self,
        request: DeleteTagRequest,
        context: ServicerContext,
    ) -> DeleteTagResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT,
                    "id is required", response=DeleteTagResponse(),
                )
            deleted = await self._tag_service.delete_tag(
                request.id,
                await self._context.create(request.user_id),
            )
            return DeleteTagResponse(deleted=bool(deleted))
        except (TagServiceError, ValueError) as exc:
            return self._fail(
                context, grpc.StatusCode.INVALID_ARGUMENT, str(exc),
                response=DeleteTagResponse(),
            )
        except Exception:
            self.log.error(
                f"DeleteTag failed: {traceback.format_exc()}\nrequest: {request}"
            )
            return self._fail(
                context, grpc.StatusCode.INTERNAL, "Internal error",
                response=DeleteTagResponse(),
            )

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _require_user_id(user_id: str) -> None:
        """Raise ``ValueError`` when ``user_id`` is empty."""
        if not user_id:
            raise ValueError("user_id is required")

    @staticmethod
    def _fail(
        context: ServicerContext,
        code: grpc.StatusCode,
        message: str,
        response: object = None,
    ):
        """Set the gRPC error code/details on ``context`` and return ``response``."""
        context.set_code(code)
        context.set_details(message)
        return response


__all__ = ["GrpcTagService"]
