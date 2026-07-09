"""Unit tests for :class:`src.grpc_mod.thirdparty_migrations_service.GrpcThirdpartyMigrationsService`.

Wire shape asserted:

* A streamed ``BookstackBookImport`` RPC reassembles chunked bytes
  into the full zip buffer before delegating to the service layer.
* ``user_id`` is read from the first chunk only; later chunks may
  leave it empty.
* Empty content + missing ``user_id`` map to ``INVALID_ARGUMENT``;
  ``BookstackZipError`` from the service layer also maps to
  ``INVALID_ARGUMENT`` (matching how other gRPC adapters treat
  bad input).
* The response message carries every field of
  :class:`src.services.thirdparty_migrations.MigrationResult`,
  including the per-chapter breakdown.
"""

from __future__ import annotations

from typing import AsyncIterator, List, Optional, cast

import grpc
import pytest
from grpc.aio import ServicerContext

from src.api.user_context import UserContextABC
from src.grpc_mod.proto.thirdparty_migrations_pb2 import BookstackBookImportChunk
from src.grpc_mod.thirdparty_migrations_service import (
    GrpcThirdpartyMigrationsService,
)
from src.services.thirdparty_migrations import (
    ImportedChapter,
    MigrationResult,
    ThirdpartyMigrationsServiceABC,
)
from src.services.thirdparty_migrations.bookstack_reader import BookstackZipError
from tests.stubs.user_context import _UserContextFactory
from tests.stubs.logging import silent_logger


class _FakeContext:
    def __init__(self) -> None:
        self.code: Optional[grpc.StatusCode] = None
        self.details: Optional[str] = None

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


class _StubMigrationsService(ThirdpartyMigrationsServiceABC):
    """Records the call; returns a preset :class:`MigrationResult`."""

    def __init__(self, result: Optional[MigrationResult] = None) -> None:
        self._result = result or MigrationResult(
            root_directory_id="root-1",
            pages_imported=3,
            attachments_uploaded=5,
            chapters=[
                ImportedChapter(
                    directory_id="chap-1",
                    chapter_name="Chapter 1",
                    pages_imported=2,
                ),
                ImportedChapter(
                    directory_id="chap-2",
                    chapter_name="Chapter 2",
                    pages_imported=1,
                ),
            ],
        )
        self.last_content: Optional[bytes] = None
        self.last_user_id: Optional[str] = None
        self.migrate_should_raise: Optional[Exception] = None

    async def migrate(
        self, content: bytes, user_ctx: UserContextABC
    ) -> MigrationResult:
        self.last_content = content
        self.last_user_id = user_ctx.user_id
        if self.migrate_should_raise is not None:
            raise self.migrate_should_raise
        return self._result


def _make_grpc(stub: ThirdpartyMigrationsServiceABC) -> GrpcThirdpartyMigrationsService:
    return GrpcThirdpartyMigrationsService(
        migrations_service=stub,
        log=silent_logger,
        context_factory=_UserContextFactory(),
    )


def _chunks(
    *items: tuple[str, bytes],
) -> AsyncIterator[BookstackBookImportChunk]:
    async def _gen() -> AsyncIterator[BookstackBookImportChunk]:
        for user_id, content in items:
            yield BookstackBookImportChunk(user_id=user_id, content=content)

    return _gen()


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassembles_chunks_and_delegates() -> None:
    stub = _StubMigrationsService()
    service = _make_grpc(stub)
    context = _FakeContext()

    response = await service.BookstackBookImport(
        _chunks(
            ("u-1", b"head"),
            ("", b"-middle"),
            ("", b"-tail"),
        ),
        cast(ServicerContext, context),
    )

    assert stub.last_user_id == "u-1"
    assert stub.last_content == b"head-middle-tail"
    assert context.code is None
    assert response.book_directory_id == "root-1"
    assert response.pages_imported == 3
    assert response.attachments_uploaded == 5
    assert len(response.chapters) == 2
    assert response.chapters[0].directory_id == "chap-1"
    assert response.chapters[1].pages_imported == 1


@pytest.mark.asyncio
async def test_first_chunk_without_user_id_is_invalid() -> None:
    stub = _StubMigrationsService()
    service = _make_grpc(stub)
    context = _FakeContext()

    response = await service.BookstackBookImport(
        _chunks(("", b"some bytes")),
        cast(ServicerContext, context),
    )

    assert response.book_directory_id == ""
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert "user_id" in (context.details or "")


@pytest.mark.asyncio
async def test_empty_stream_is_invalid() -> None:
    stub = _StubMigrationsService()
    service = _make_grpc(stub)
    context = _FakeContext()

    async def empty_stream() -> AsyncIterator[BookstackBookImportChunk]:
        if False:
            yield BookstackBookImportChunk()  # pragma: no cover

    response = await service.BookstackBookImport(
        empty_stream(),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_only_user_id_chunk_then_empty_content_is_invalid() -> None:
    """A stream with only `user_id` and no bytes must fail loudly."""
    stub = _StubMigrationsService()
    service = _make_grpc(stub)
    context = _FakeContext()

    response = await service.BookstackBookImport(
        _chunks(("u-1", b"")),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert "content" in (context.details or "")


@pytest.mark.asyncio
async def test_bookstack_zip_error_maps_to_invalid_argument() -> None:
    stub = _StubMigrationsService()
    stub.migrate_should_raise = BookstackZipError("not a valid zip file")
    service = _make_grpc(stub)
    context = _FakeContext()

    response = await service.BookstackBookImport(
        _chunks(("u-1", b"junk")),
        cast(ServicerContext, context),
    )

    assert response.book_directory_id == ""
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert "not a valid zip" in (context.details or "")


@pytest.mark.asyncio
async def test_permission_error_maps_to_permission_denied() -> None:
    stub = _StubMigrationsService()
    stub.migrate_should_raise = PermissionError("not allowed")
    service = _make_grpc(stub)
    context = _FakeContext()

    response = await service.BookstackBookImport(
        _chunks(("u-1", b"junk")),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_unexpected_error_maps_to_internal() -> None:
    stub = _StubMigrationsService()
    stub.migrate_should_raise = RuntimeError("boom")
    service = _make_grpc(stub)
    context = _FakeContext()

    response = await service.BookstackBookImport(
        _chunks(("u-1", b"junk")),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INTERNAL
    assert "Internal server error" in (context.details or "")


@pytest.mark.asyncio
async def test_response_with_no_chapters_yields_empty_list() -> None:
    """A migration with no chapters should still return a clean response."""
    stub = _StubMigrationsService(
        MigrationResult(
            root_directory_id="root-x",
            pages_imported=1,
            attachments_uploaded=0,
            chapters=[],
        )
    )
    service = _make_grpc(stub)
    context = _FakeContext()

    response = await service.BookstackBookImport(
        _chunks(("u-1", b"data")),
        cast(ServicerContext, context),
    )

    assert context.code is None
    assert response.book_directory_id == "root-x"
    assert list(response.chapters) == []