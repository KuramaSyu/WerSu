from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Dict

from src.api.undefined import UNDEFINED
from src.db.repos.attachments.attachments import (
    Attachment,
    AttachmentsMetadataRepoABC,
    AttachmentsRepoABC,
)
from src.db.table import TableABC


class InMemoryAttachmentRepo(AttachmentsRepoABC):
    """In-memory attachment storage for unit tests."""

    def __init__(self) -> None:
        self._store: Dict[str, bytes] = {}

    async def post_attachment(self, attachment: Attachment) -> str:
        if attachment.key is UNDEFINED or attachment.key is None:
            key = f"mem-{len(self._store) + 1}"
        else:
            key = attachment.key
        self._store[str(key)] = attachment.content
        return str(key)

    async def get_attachment(self, key: str) -> Attachment:
        if key not in self._store:
            raise KeyError(f"Attachment not found for key={key}")
        content = self._store[key]
        now = datetime.utcnow().isoformat()
        return Attachment(
            key=key,
            filename=key,
            filepath=key,
            content_type="application/octet-stream",
            size=len(content),
            created_at=now,
            updated_at=now,
            content=content,
        )

    async def delete_attachment(self, key: str) -> None:
        self._store.pop(key, None)


class InMemoryAttachmentMetadataRepo(AttachmentsMetadataRepoABC):
    """In-memory metadata store for unit tests."""

    def __init__(self) -> None:
        self._metadata: Dict[str, Attachment] = {}

    async def post_metadata(
        self, attachment: Attachment, user_ctx: UserContextABC | None = None
    ) -> None:
        del user_ctx
        if attachment.key is UNDEFINED or attachment.key is None:
            raise ValueError("Attachment key must be set before storing metadata")
        self._metadata[str(attachment.key)] = replace(attachment, content=b"")

    async def get_metadata(
        self, key: str, user_ctx: UserContextABC | None = None
    ) -> Attachment:
        del user_ctx
        if key not in self._metadata:
            raise KeyError(f"Attachment metadata not found for key={key}")
        return self._metadata[key]

    async def update_metadata(
        self, attachment: Attachment, user_ctx: UserContextABC | None = None
    ) -> Attachment:
        del user_ctx
        if attachment.key is UNDEFINED or attachment.key is None:
            raise ValueError("Attachment key must be given perform an update")
        self._metadata[str(attachment.key)] = replace(attachment, content=b"")
        return self._metadata[str(attachment.key)]

    async def delete_metadata(
        self, key: str, user_ctx: UserContextABC | None = None
    ) -> None:
        del user_ctx
        self._metadata.pop(key, None)
