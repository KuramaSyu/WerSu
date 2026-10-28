"""gRPC adapter for :class:`src.services.attachment_facade.AttachmentFacadeABC`.

Implements ``AttachmentService`` from ``grpc/proto/attachments.proto``:
upload, fetch, fetch metadata, update metadata, delete, plus the
note-link / note-unlink pair.  The proto ``request.filepath``
currently doubles as the S3 key inside the bucket; the wire
contract is preserved verbatim while delegating to the attachment
facade.
"""

from __future__ import annotations

import traceback
from datetime import datetime

import grpc
from google.protobuf.empty_pb2 import Empty
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import ContextFactory, UserContextABC
from src.db.repos.attachments.attachments import Attachment as AttachmentEntity
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.attachments_pb2 import (
    Attachment as GrpcAttachment,
    AttachmentMetadata as GrpcAttachmentMetadata,
    DeleteAttachmentLinkRequest,
    DeleteAttachmentRequest,
    DeleteAttachmentResponse,
    GetAttachmentMetadataRequest,
    GetAttachmentRequest,
    GetAttachmentTokenRequest,
    GetAttachmentTokenResponse,
    PostAttachmentLinkRequest,
    PostAttachmentRequest,
    UpdateAttachmentMetadataRequest,
)
from src.grpc_mod.proto.attachments_pb2_grpc import AttachmentServiceServicer
from src.services.attachment_facade import AttachmentFacadeABC


class GrpcAttachmentService(AttachmentServiceServicer):
    """Implements the gRPC service defined in grpc/proto/attachments.proto."""

    def __init__(
        self,
        attachment_service: AttachmentFacadeABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ):
        self.attachment_service = attachment_service
        self.log = log(__name__, self)
        self._to_grpc = to_grpc
        self._context = context_factory

    @log_service_call()
    async def PostAttachment(
        self, request: PostAttachmentRequest, context: ServicerContext
    ) -> GrpcAttachment:
        try:
            now = datetime.now()
            attachment = AttachmentEntity(
                key=request.filepath,  # since filepath is currently unused, REST uses it for the S3 path with bucket
                filename=request.filename,
                filepath=request.filepath,
                content_type=request.content_type or "application/octet-stream",
                size=len(request.content),
                created_at=now,
                updated_at=now,
                content=request.content,
            )

            created = await self.attachment_service.post_attachment(attachment, await self._context.create(request.user_id))
            return created.convert(self._to_grpc)
        except ValueError as exc:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return GrpcAttachment()
        except Exception:
            self.log.error(f"Error creating attachment: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while creating attachment")
            return GrpcAttachment()

    @log_service_call()
    async def GetAttachment(
        self, request: GetAttachmentRequest, context: ServicerContext
    ) -> GrpcAttachment:
        try:
            self.log.debug(f"Fetching attachment with key={request.key} for user_id={request.user_id}")
            attachment = await self.attachment_service.get_attachment(request.key, await self._context.create(request.user_id))
            return attachment.convert(self._to_grpc)
        except KeyError as exc:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return GrpcAttachment()
        except Exception:
            self.log.error(f"Error fetching attachment: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching attachment")
            return GrpcAttachment()

    @log_service_call()
    async def GetAttachmentMetadata(
        self, request: GetAttachmentMetadataRequest, context: ServicerContext
    ) -> GrpcAttachmentMetadata:
        try:
            attachment = await self.attachment_service.get_metadata(request.key, await self._context.create(request.user_id))
            return self._to_grpc.visit_attachment_metadata(attachment)
        except KeyError as exc:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return GrpcAttachmentMetadata()
        except Exception:
            self.log.error(f"Error fetching attachment metadata: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching attachment metadata")
            return GrpcAttachmentMetadata()

    @log_service_call()
    async def DeleteAttachment(
        self, request: DeleteAttachmentRequest, context: ServicerContext
    ) -> DeleteAttachmentResponse:
        try:
            await self.attachment_service.delete_attachment(request.key, await self._context.create(request.user_id))
            return DeleteAttachmentResponse(success=True)
        except Exception:
            self.log.error(f"Error deleting attachment: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while deleting attachment")
            return DeleteAttachmentResponse(success=False)

    @log_service_call()
    async def PostAttachmentLink(self, request: PostAttachmentLinkRequest, context: ServicerContext) -> Empty:
        try:
            sub_type, sub_id = _resolve_link_target(
                request.link_target, request.note_id_link, request.user_id_link, context
            )
            if sub_type is None:
                return Empty()
            await self.attachment_service.link_attachment(
                attachment_key=request.attachment_key,
                sub_type=sub_type,
                sub_id=sub_id,
                user_ctx=await self._context.create(request.user_id),
            )
        except Exception:
            self.log.error(f"Error linking attachment: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while linking attachment")
        return Empty()

    @log_service_call()
    async def UpdateAttachmentMetadata(self, request: UpdateAttachmentMetadataRequest, context: ServicerContext) -> GrpcAttachmentMetadata:
        attachment = AttachmentEntity(
            key=request.key or UNDEFINED,
            filename=request.filename or UNDEFINED,
            content_type=request.content_type or UNDEFINED,
        )
        try:
            updated = await self.attachment_service.update_metadata(
                attachment,
                user_ctx=await self._context.create(request.user_id),
            )
            return self._to_grpc.visit_attachment_metadata(updated)
        except Exception:
            self.log.error(f"Error updating attachment metadata: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while updating attachment metadata")
            return GrpcAttachmentMetadata()

    @log_service_call()
    async def GetAttachmentToken(
        self, request: GetAttachmentTokenRequest, context: ServicerContext
    ) -> GetAttachmentTokenResponse:
        try:
            token = await self.attachment_service.get_attachment_token(
                request.attachment_key,
                await self._context.create(request.user_id),
            )
            return GetAttachmentTokenResponse(token=token)
        except PermissionError as exc:
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(exc))
            return GetAttachmentTokenResponse()
        except Exception:
            self.log.error(f"Error getting attachment token: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while getting attachment token")
            return GetAttachmentTokenResponse()

    @log_service_call()
    async def DeleteAttachmentLink(self, request: DeleteAttachmentLinkRequest, context: ServicerContext) -> Empty:
        try:
            sub_type, sub_id = _resolve_link_target(
                request.link_target, request.note_id, request.user_id_link, context
            )
            if sub_type is None:
                return Empty()
            await self.attachment_service.unlink_attachment(
                attachment_key=request.attachment_key,
                sub_type=sub_type,
                sub_id=sub_id,
                user_ctx=await self._context.create(request.user_id),
            )
        except Exception:
            self.log.error(f"Error linking attachment: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while unlinking attachment")
        return Empty()


def _resolve_link_target(
    link_target: int,
    note_id_link: str,
    user_id_link: str,
    context: ServicerContext,
) -> tuple[str | None, str]:
    """Translate the wire LinkTarget enum to a (sub_type, sub_id) pair."""
    if link_target == PostAttachmentLinkRequest.LINK_TARGET_USER:
        if not user_id_link:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(
                "user_id_link is required when link_target == LINK_TARGET_USER"
            )
            return None, ""
        return "user", user_id_link
    # UNSPECIFIED + NOTE share the same code path: legacy clients
    # never set link_target and always set the note id.
    if not note_id_link:
        context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
        context.set_details(
            "note_id_link is required when link_target == LINK_TARGET_NOTE"
        )
        return None, ""
    return "note", note_id_link