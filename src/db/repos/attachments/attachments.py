from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Protocol, Callable

from asyncpg import Record
from sympy import Q


from src.api.other.undefined import UNDEFINED, UndefinedOr, is_undefined
from src.api import UserContextABC
from src.api.other.visitor import AcceptsVisitor, EntityVisitor
from src.db.table import TableABC
from src.utils import convert_entity_for_db, asdict



@dataclass
class Attachment:
    """In-memory representation of an attachment.

    Notes
    -----
    - ``key`` is optional and is assigned by storage backends.
    - ``content`` is expected to be raw bytes for upload/download.
    - ``created_at`` / ``updated_at`` are kept as ISO strings for transport.
    """

    key: UndefinedOr[str]  # set it after upload
    filename: UndefinedOr[str] = UNDEFINED
    filepath: UndefinedOr[str] = UNDEFINED
    content_type: UndefinedOr[str] = UNDEFINED
    size: UndefinedOr[int] = UNDEFINED
    content: UndefinedOr[bytes] = UNDEFINED
    
    created_at: UndefinedOr[datetime] = UNDEFINED  # set by storage repo if not provided
    updated_at: UndefinedOr[datetime] = UNDEFINED  # set by storage repo if not provided
    checksum: UndefinedOr[str] = UNDEFINED

    @property
    def sha256(self) -> str:
        if self.checksum is not UNDEFINED:
            return str(self.checksum)
        return hashlib.sha256(self.content).hexdigest()
    
    @property
    def get_size(self) -> int:
        if self.size is not UNDEFINED:
            return self.size
        return len(self.content)

    def visit(self, visitor: EntityVisitor):
        """Dispatch this attachment to ``visitor.visit_attachment``."""
        return visitor.visit_attachment(self)

    def convert(self, visitor: EntityVisitor):
        """Alias for :meth:`visit`."""
        return self.visit(visitor)


class AttachmentRepoABC(ABC):
    @abstractmethod
    async def post_attachment(self, attachment: Attachment) -> str:
        """Upload an attachment and return its key."""
        ...
    
    @abstractmethod
    async def get_attachment(self, key: str) -> Attachment:
        """Download an attachment by key.

        Raises
        ------
        KeyError
            If the attachment is not found for the given key.
        
        """
        ...
    
    @abstractmethod
    async def delete_attachment(self, key: str) -> None:
        """Delete an attachment by key."""
        ...

class AttachmentMetadataRepoABC(ABC):
    @abstractmethod
    async def post_metadata(self, attachment: Attachment, user_ctx: UserContextABC) -> None:
        """Save attachment metadata to the database."""
        ...
    
    @abstractmethod
    async def update_metadata(self, attachment: Attachment, user_ctx: UserContextABC) -> Attachment:
        """Searches attachment by key and updates all other fields which are not UNDEFINED"""
        ...
    @abstractmethod
    async def get_metadata(self, key: str) -> Attachment:
        """Get attachment metadata by key."""
        ...
    
    @abstractmethod
    async def delete_metadata(self, key: str) -> None:
        """Delete attachment metadata by key."""
        ...


class AttachmentMetadataPostgresRepo(AttachmentMetadataRepoABC):
    """Postgres-backed metadata repository for attachments."""

    def __init__(self, table: TableABC):
        self._table = table

    async def post_metadata(self, attachment: Attachment, user_ctx: UserContextABC) -> None:
        if attachment.key is UNDEFINED:
            raise ValueError("Attachment key must be set before storing metadata")
        
        normalized = convert_entity_for_db(attachment)
        where = {
            "key": normalized.key,
            "filename": normalized.filename,
            "filepath": normalized.filepath,
            "content_type": normalized.content_type,
            "size": normalized.size,
            "created_at": normalized.created_at,
            "updated_at": normalized.updated_at,
            "created_by": user_ctx.user_id,
            "sha256": normalized.sha256
        }

        await self._table.insert(where, returning="key",
        )

    async def update_metadata(self, attachment: Attachment, user_ctx: UserContextABC) -> Attachment:
        if attachment.key is UNDEFINED:
            raise ValueError("Attachment key must be set for update")
        
        set_values = {}
        if not is_undefined(attachment.filename): set_values["filename"] = attachment.filename
        if not is_undefined(attachment.content_type): set_values["content_type"] = attachment.content_type
        if not is_undefined(attachment.content):
            set_values["size"] = attachment.get_size()
            set_values["sha256"] = attachment.sha256
        set_values["updated_at"] = datetime.now()
        
        where = {"key": attachment.key}

        updated_record = await self._table.update(set_values, where, returning="key, filename, filepath, content_type, size, created_at, updated_at, sha256")
        if not updated_record:
            raise KeyError(f"Attachment metadata not found for key={attachment.key}")
        
        return Attachment(
            key=updated_record["key"],
            filename=updated_record["filename"],
            filepath=updated_record["filepath"],
            content_type=updated_record["content_type"],
            size=updated_record["size"],
            created_at=updated_record["created_at"],
            updated_at=updated_record["updated_at"],
            content=UNDEFINED,  # content is not stored in metadata repo
            checksum=updated_record["sha256"],
        )

    async def get_metadata(self, key: str) -> Attachment:
        record = await self._table.select_row(
            where={"key": key},
            select="key, filename, filepath, content_type, size, created_at, updated_at, sha256",
        )
        if not record:
            raise KeyError(f"Attachment metadata not found for key={key}")
        

        return Attachment(
            key=record["key"],
            filename=record["filename"],
            filepath=record["filepath"],
            content_type=record["content_type"],
            size=record["size"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
            content=b"",
            checksum=record["sha256"],
        )

    async def delete_metadata(self, key: str) -> None:
        await self._table.delete(where={"key": key}, returning="key")


class AttachmentS3Repo(AttachmentRepoABC):
    """S3-compatible attachment repository using a sync boto3 client.

    Notes
    -----
    boto3 is synchronous; we delegate calls to a worker thread via
    ``asyncio.to_thread`` to keep async callers responsive.
    """

    def __init__(self, client: "S3ClientProtocol", bucket: str, key_prefix: str = "attachments/", get_now: Callable[[], datetime] = lambda: datetime.now()):
        self._client = client
        self._bucket = bucket
        self._key_prefix = key_prefix.rstrip("/") + "/" if key_prefix else ""
        self._get_now = get_now

    async def post_attachment(self, attachment: Attachment) -> str:
        key = attachment.key
        if key is UNDEFINED or key is None:
            key = f"{self._key_prefix}{_new_key()}"

        content_type = attachment.content_type or "application/octet-stream"
        body = attachment.content
        size = attachment.size or len(body)
        attachment.size = size

        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            ContentLength=size,
        )
        return str(key)

    async def get_attachment(self, key: str) -> Attachment:
        def _download():
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"].read()
            return response, body

        response, body = await asyncio.to_thread(_download)
        content_type = response.get("ContentType") or "application/octet-stream"
        size = response.get("ContentLength") or len(body)
        last_modified = response.get("LastModified")
        updated_at = last_modified or self._get_now()

        return Attachment(
            key=key,
            filename=key.split("/")[-1],
            filepath=key,
            content_type=content_type,
            size=size,
            created_at=updated_at,
            updated_at=updated_at,
            content=body,
        )

    async def delete_attachment(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)


class S3ClientProtocol(Protocol):
    """Subset of boto3 S3 client used by ``AttachmentS3Repo``."""

    def put_object(self, **kwargs) -> dict:  # pragma: no cover - protocol definition
        ...

    def get_object(self, **kwargs) -> dict:  # pragma: no cover - protocol definition
        ...

    def delete_object(self, **kwargs) -> dict:  # pragma: no cover - protocol definition
        ...


def _new_key() -> str:
    """Generate a storage key for attachments."""
    from uuid import uuid4

    return f"{uuid4()}"


def _to_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.utcnow()
    return datetime.utcnow()

