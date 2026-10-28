"""Fast unit tests for :class:`src.grpc_mod.attachment_service.GrpcAttachmentService`.

The adapter is intentionally thin: the permission check and token
minting live in the service layer
(:class:`src.services.attachment_facade.AttachmentFacadeImpl`).  These
tests pin the adapter's wire contract:

* :meth:`GetAttachmentToken` forwards the token returned by the
  service into ``GetAttachmentTokenResponse.token``.
* A :exc:`PermissionError` raised by the service maps to
  ``PERMISSION_DENIED`` with the service's message and an empty token.
"""

from __future__ import annotations

import logging
from typing import cast

import grpc

from tests.stubs.user_context import _UserContextFactory
from src.api.other.user_context import UserContextABC
from src.api.services.attachment_facade import AttachmentFacadeABC
from src.grpc_mod.attachment_service import GrpcAttachmentService
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.attachments_pb2 import (
    GetAttachmentTokenRequest,
    GetAttachmentTokenResponse,
)


class _FakeContext:
    def __init__(self) -> None:
        self.code: grpc.StatusCode | None = None
        self.details: str | None = None

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


class _StubAttachmentService(AttachmentFacadeABC):
    """Stub whose ``get_attachment_token`` behavior is preset per test."""

    def __init__(self, *, token: str = "", error: Exception | None = None) -> None:
        self._token = token
        self._error = error
        self.last_key: str | None = None
        self.last_user_ctx: UserContextABC | None = None

    async def get_attachment_token(self, attachment_key, user_ctx):
        self.last_key = attachment_key
        self.last_user_ctx = user_ctx
        if self._error is not None:
            raise self._error
        return self._token

    # The remaining ABC surface is unused by these tests.
    async def post_attachment(self, attachment, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def update_metadata(self, attachment, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def get_attachment(self, key, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def get_metadata(self, key, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def delete_attachment(self, key, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def link_attachment(self, attachment_key, sub_type, sub_id, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def unlink_attachment(self, attachment_key, sub_type, sub_id, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def list_attachments(self, sub_type, sub_id, user_ctx):  # pragma: no cover
        raise NotImplementedError


def _log_provider(*_args, **_kwargs):
    return logging.getLogger("test.grpc.attachment")


def _make_service(stub: _StubAttachmentService) -> GrpcAttachmentService:
    return GrpcAttachmentService(
        attachment_service=stub,
        log=_log_provider,
        to_grpc=ConvertToGrpcVisitor(),
        context_factory=_UserContextFactory(),
    )


async def test_get_attachment_token_forwards_token_from_service() -> None:
    stub = _StubAttachmentService(token="jwt:bob:att-1")
    service = _make_service(stub)
    context = _FakeContext()

    request = GetAttachmentTokenRequest(attachment_key="att-1", user_id="bob")
    response = await service.GetAttachmentToken(request, cast(grpc.aio.ServicerContext, context))

    assert isinstance(response, GetAttachmentTokenResponse)
    assert context.code is None
    assert response.token == "jwt:bob:att-1"
    assert stub.last_key == "att-1"
    assert stub.last_user_ctx is not None
    assert stub.last_user_ctx.user_id == "bob"


async def test_get_attachment_token_maps_permission_error_to_denied() -> None:
    stub = _StubAttachmentService(
        error=PermissionError(
            "user has no permission to view attachment att-2"
        )
    )
    service = _make_service(stub)
    context = _FakeContext()

    request = GetAttachmentTokenRequest(attachment_key="att-2", user_id="bob")
    response = await service.GetAttachmentToken(request, cast(grpc.aio.ServicerContext, context))

    assert isinstance(response, GetAttachmentTokenResponse)
    assert response.token == ""
    assert context.code == grpc.StatusCode.PERMISSION_DENIED
    assert context.details == "user has no permission to view attachment att-2"
