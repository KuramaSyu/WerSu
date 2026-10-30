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

from tests.doubles import _StubUserContextFactory
from src.api.other.user_context import UserContextABC
from src.api.services.attachment_facade import AttachmentFacadeABC
from src.grpc_mod.attachment_service import GrpcAttachmentService
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.attachments_pb2 import (
    GetAttachmentTokenRequest,
    GetAttachmentTokenResponse,
)

from tests.doubles.dummies.fake_context import _DummyContext
from tests.doubles.fakespys.grpc_attachment_service import _FakeSpyAttachmentService






def _log_provider(*_args, **_kwargs):
    return logging.getLogger("test.grpc.attachment")


def _make_service(stub: _FakeSpyAttachmentService) -> GrpcAttachmentService:
    return GrpcAttachmentService(
        attachment_service=stub,
        log=_log_provider,
        to_grpc=ConvertToGrpcVisitor(),
        context_factory=_StubUserContextFactory(),
    )


async def test_get_attachment_token_forwards_token_from_service() -> None:
    stub = _FakeSpyAttachmentService(token="jwt:bob:att-1")
    service = _make_service(stub)
    context = _DummyContext()

    request = GetAttachmentTokenRequest(attachment_key="att-1", user_id="bob")
    response = await service.GetAttachmentToken(request, cast(grpc.aio.ServicerContext, context))

    assert isinstance(response, GetAttachmentTokenResponse)
    assert context.code is None
    assert response.token == "jwt:bob:att-1"
    assert stub.last_key == "att-1"
    assert stub.last_user_ctx is not None
    assert stub.last_user_ctx.user_id == "bob"


async def test_get_attachment_token_maps_permission_error_to_denied() -> None:
    stub = _FakeSpyAttachmentService(
        error=PermissionError(
            "user has no permission to view attachment att-2"
        )
    )
    service = _make_service(stub)
    context = _DummyContext()

    request = GetAttachmentTokenRequest(attachment_key="att-2", user_id="bob")
    response = await service.GetAttachmentToken(request, cast(grpc.aio.ServicerContext, context))

    assert isinstance(response, GetAttachmentTokenResponse)
    assert response.token == ""
    assert context.code == grpc.StatusCode.PERMISSION_DENIED
    assert context.details == "user has no permission to view attachment att-2"
