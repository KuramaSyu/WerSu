from __future__ import annotations

from datetime import datetime

from google.protobuf.timestamp_pb2 import Timestamp

from src.api.undefined import UNDEFINED, UndefinedOr
from src.db.repos.attachments.attachments import Attachment
from src.grpc_mod.proto.attachments_pb2 import Attachment as GrpcAttachment
from src.grpc_mod.proto.attachments_pb2 import AttachmentMetadata


def to_grpc_attachment_metadata(attachment: Attachment | None) -> AttachmentMetadata:
    """Convert an Attachment entity into gRPC AttachmentMetadata."""
    if attachment is None or attachment.key is UNDEFINED:
        return AttachmentMetadata()

    created_at_ts = _to_timestamp(attachment.created_at)
    updated_at_ts = _to_timestamp(attachment.updated_at)

    return AttachmentMetadata(
        key=str(attachment.key),
        filename=attachment.filename,
        filepath=attachment.filepath,
        content_type=attachment.content_type,
        size=attachment.size,
        created_at=created_at_ts,
        updated_at=updated_at_ts,
        sha256=attachment.sha256,
    )


def to_grpc_attachment(attachment: Attachment | None) -> GrpcAttachment:
    """Convert an Attachment entity into gRPC Attachment."""
    if attachment is None:
        return GrpcAttachment()

    return GrpcAttachment(
        metadata=to_grpc_attachment_metadata(attachment),
        content=attachment.content,
    )


def _to_timestamp(value: UndefinedOr[datetime]) -> Timestamp:
    ts = Timestamp()
    if not isinstance(value, datetime):
        return ts
    try:
        dt = value
    except ValueError:
        dt = datetime.now()
    ts.FromDatetime(dt)
    return ts
