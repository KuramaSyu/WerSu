from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime, timezone
import time

from src.api import LoggingProvider
from src.api.undefined import UNDEFINED
from src.db.repos.attachments.attachments import (
    Attachment,
    AttachmentsMetadataRepoABC,
    AttachmentsRepoABC,
)
from src.db.table import TableABC


class AttachmentFacadeABC(ABC):
    """Application service for attachment lifecycle."""

    @abstractmethod
    async def post_attachment(self, attachment: Attachment) -> Attachment:
        """Upload attachment contents and persist metadata."""
        ...

    @abstractmethod
    async def get_attachment(self, key: str) -> Attachment:
        """Fetch attachment metadata and contents by key."""
        ...

    @abstractmethod
    async def get_metadata(self, key: str) -> Attachment:
        """Fetch attachment metadata without the content payload."""
        ...

    @abstractmethod
    async def delete_attachment(self, key: str) -> None:
        """Delete attachment metadata and contents."""
        ...

    @abstractmethod
    async def link_attachment_to_note(self, attachment_key: str, note_id: str) -> None:
        """Create a link between an attachment and a note. This is a separate step to allow linking an attachment to multiple notes."""
        ...
    
    @abstractmethod
    async def unlink_attachment_from_note(self, attachment_key: str, note_id: str) -> None:
        """Remove a link between an attachment and a note."""
        ...

    @abstractmethod
    async def list_attachments_for_note(self, note_id: str) -> list[Attachment]:
        """List all attachments linked to a given note."""
        ...


class AttachmentFacade(AttachmentFacadeABC):
    """Facade that combines object storage + metadata storage which also creates links between attachments and notes."""

    def __init__(
        self,
        attachment_repo: AttachmentsRepoABC,
        metadata_repo: AttachmentsMetadataRepoABC,
        attachments_note_link_table: TableABC,
        log: LoggingProvider,
    ) -> None:
        self._attachment_repo = attachment_repo
        self._metadata_repo = metadata_repo
        self._attachments_note_link_table = attachments_note_link_table
        self.log = log(__name__, self)

    async def post_attachment(self, attachment: Attachment) -> Attachment:
        if attachment.content is None:
            raise ValueError("Attachment content cannot be empty")

        now = datetime.now(timezone.utc).isoformat()
        if attachment.created_at is UNDEFINED:
            attachment.created_at = now
        if attachment.updated_at is UNDEFINED:
            attachment.updated_at = now

        # send content to object storage
        key = await self._attachment_repo.post_attachment(attachment)
        attachment.key = key  # assign generated key back to attachment

        # persist metadata to the database, using key given from object storage
        await self._metadata_repo.post_metadata(attachment)
        self.log.debug(f"Stored attachment metadata for {key=}")
        return attachment

    async def get_attachment(self, key: str) -> Attachment:
        metadata = await self._metadata_repo.get_metadata(key)
        content_attachment = await self._attachment_repo.get_attachment(key)

        # Use metadata as the source of truth for descriptive fields.
        return Attachment(
            key=key,
            filename=metadata.filename,
            filepath=metadata.filepath,
            content_type=metadata.content_type,
            size=metadata.size,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
            content=content_attachment.content,
            checksum=metadata.checksum,
        )

    async def get_metadata(self, key: str) -> Attachment:
        return await self._metadata_repo.get_metadata(key)

    async def delete_attachment(self, key: str) -> None:
        # Remove object payload first, then metadata.
        await self._attachment_repo.delete_attachment(key)
        await self._metadata_repo.delete_metadata(key)


    async def link_attachment_to_note(self, attachment_key: str, note_id: str) -> None:
        await self._attachments_note_link_table.insert(
            {"note_id": note_id, "attachment_key": attachment_key}
        )

    async def unlink_attachment_from_note(self, attachment_key: str, note_id: str) -> None:
        await self._attachments_note_link_table.delete(
            {"note_id": note_id, "attachment_key": attachment_key}
        )

    async def list_attachments_for_note(self, note_id: str) -> list[Attachment]:
        # fetch all attachment links from db and then fetch each attachment from object storage
        links = await self._attachments_note_link_table.select(where={"note_id": note_id})
        attachments = []
        for link in links:
            attachment = await self.get_attachment(link["attachment_key"])
            attachments.append(attachment)
        return attachments