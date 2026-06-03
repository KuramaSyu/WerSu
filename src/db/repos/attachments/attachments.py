from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Protocol

from asyncpg import Record

from src.api.undefined import UNDEFINED, UndefinedOr
from src.db.table import TableABC

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
    filename: str
    filepath: str
    content_type: str
    size: int
    created_at: str
    updated_at: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class AttachmentsRepo(ABC):
    @abstractmethod
    async def post_attachment(self, attachment: Attachment) -> str:
        """Upload an attachment and return its key."""
        ...
    
    @abstractmethod
    async def get_attachment(self, key: str) -> Attachment:
        """Download an attachment by key."""
        ...
    
    @abstractmethod
    async def delete_attachment(self, key: str) -> None:
        """Delete an attachment by key."""
        ...

class AttachmentsMetadataRepo(ABC):
    @abstractmethod
    async def post_metadata(self, attachment: Attachment) -> None:
        """Save attachment metadata to the database."""
        ...
    
    @abstractmethod
    async def get_metadata(self, key: str) -> Attachment:
        """Get attachment metadata by key."""
        ...
    
    @abstractmethod
    async def delete_metadata(self, key: str) -> None:
        """Delete attachment metadata by key."""
        ...


class AttachmentsMetadataPostgresRepo(AttachmentsMetadataRepo):
    """Postgres-backed metadata repository for attachments."""

    def __init__(self, table: TableABC[List[Record]]):
        self._table = table

    async def post_metadata(self, attachment: Attachment) -> None:
        if attachment.key is UNDEFINED:
            raise ValueError("Attachment key must be set before storing metadata")

        await self._table.insert(
            {
                "key": attachment.key,
                "filename": attachment.filename,
                "filepath": attachment.filepath,
                "content_type": attachment.content_type,
                "size": attachment.size,
                "created_at": attachment.created_at,
                "updated_at": attachment.updated_at,
                "sha256": attachment.sha256,
            },
            returning="key",
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
            created_at=_to_iso_string(record["created_at"]),
            updated_at=_to_iso_string(record["updated_at"]),
            content=b"",
        )

    async def delete_metadata(self, key: str) -> None:
        await self._table.delete(where={"key": key}, returning="key")


class AttachmentsS3Repo(AttachmentsRepo):
    """S3-compatible attachment repository using a sync boto3 client.

    Notes
    -----
    boto3 is synchronous; we delegate calls to a worker thread via
    ``asyncio.to_thread`` to keep async callers responsive.
    """

    def __init__(self, client: "S3ClientProtocol", bucket: str, key_prefix: str = "attachments/"):
        self._client = client
        self._bucket = bucket
        self._key_prefix = key_prefix.rstrip("/") + "/" if key_prefix else ""

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
        updated_at = _to_iso_string(last_modified) if last_modified else _now_iso()

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
    """Subset of boto3 S3 client used by ``AttachmentsS3Repo``."""

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


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _to_iso_string(value: Optional[object]) -> str:
    if value is None:
        return _now_iso()
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
