"""gRPC adapter for :class:`~src.services.thirdparty_migrations.ThirdpartyMigrationsServiceABC`.

Translates the client-streaming ``BookstackBookImport`` RPC into a
single :meth:`ThirdpartyMigrationsServiceABC.migrate` call.

Behaviour:
    - Reads ``user_id`` from the first chunk; subsequent chunks may
      leave it empty.
    - Reassembles the zip bytes from the streamed ``content`` field
      and passes the joined buffer to the service.
    - Maps service-layer exceptions to gRPC status codes via the
      shared ``_set_context_error`` helper (same shape as the other
      adapters in this package).
"""

from __future__ import annotations

import traceback
from typing import AsyncIterator

import grpc
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.other.user_context import ContextFactory, UserContextABC
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.proto.thirdparty_migrations_pb2 import (
    BookstackBookImportChunk,
    BookstackBookImportResponse,
    BookstackImportedChapter,
)
from src.grpc_mod.proto.thirdparty_migrations_pb2_grpc import (
    ThirdpartyMigrationsServiceServicer,
)
from src.services.thirdparty_migrations import (
    ImportedChapter,
    MigrationResult,
    ThirdpartyMigrationsServiceABC,
)
from src.services.thirdparty_migrations.bookstack_reader import BookstackZipError


class GrpcThirdpartyMigrationsService(ThirdpartyMigrationsServiceServicer):
    """gRPC adapter for the third-party migration RPCs."""

    def __init__(
        self,
        migrations_service: ThirdpartyMigrationsServiceABC,
        log: LoggingProvider,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._service = migrations_service
        self.log = log(__name__, self)
        self._context = context_factory

    @log_service_call()
    async def BookstackBookImport(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        request_iterator: AsyncIterator[BookstackBookImportChunk],
        context: ServicerContext,
    ) -> BookstackBookImportResponse:
        try:
            user_id, content = await self._consume_stream(request_iterator)
            if not user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return BookstackBookImportResponse()
            if not content:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("content is empty")
                return BookstackBookImportResponse()

            user_ctx = await self._context.create(user_id)
            result = await self._service.migrate(content, user_ctx)
            return self._to_response(result)
        except Exception as exc:
            self._set_context_error(exc, context)
            return BookstackBookImportResponse()

    async def _consume_stream(
        self,
        request_iterator: AsyncIterator[BookstackBookImportChunk],
    ) -> tuple[str, bytes]:
        """Pull `user_id` from the first chunk and join the bytes payload.

        Empty chunks (no `content`) are tolerated so the client can
        signal the start of the stream with just `user_id`.
        """
        user_id = ""
        chunks: list[bytes] = []
        first = True
        async for chunk in request_iterator:
            if first:
                user_id = chunk.user_id or user_id
                first = False
            if chunk.content:
                chunks.append(chunk.content)
        return user_id, b"".join(chunks)

    @staticmethod
    def _to_response(result: MigrationResult) -> BookstackBookImportResponse:
        return BookstackBookImportResponse(
            book_directory_id=result.root_directory_id,
            pages_imported=result.pages_imported,
            attachments_uploaded=result.attachments_uploaded,
            chapters=[
                BookstackImportedChapter(
                    directory_id=ch.directory_id,
                    chapter_name=ch.chapter_name,
                    pages_imported=ch.pages_imported,
                )
                for ch in result.chapters
            ],
        )

    def _set_context_error(self, exc: Exception, context: ServicerContext) -> None:
        """Map service exceptions to gRPC status codes.

        Mirrors the helper used by
        :class:`src.grpc_mod.sharing_service.GrpcSharingService`.
        """
        if isinstance(exc, BookstackZipError):
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return
        if isinstance(exc, PermissionError):
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(exc))
            return
        if isinstance(exc, ValueError):
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return
        if isinstance(exc, LookupError):
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return

        self.log.error(
            "Error handling thirdparty_migrations request: %s",
            traceback.format_exc(),
        )
        context.set_code(grpc.StatusCode.INTERNAL)
        context.set_details("Internal server error while handling migration request")


__all__ = ["GrpcThirdpartyMigrationsService"]