from __future__ import annotations

from typing import *
from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime, timezone
import time

from src.api import LoggingProvider, UserContextABC
from src.api.undefined import UNDEFINED
from src.db.repos.attachments.attachments import (
    Attachment,
    AttachmentsMetadataRepoABC,
    AttachmentsRepoABC,
)
from src.db import PermissionRepoABC
from src.api import Relationship, ObjectRef, SubjectRef, ObjectTypeEnum, SubjectType, AttachmentRelationEnum
from src.db.table import TableABC
from src.domain.permission_chain import HasAttachmentWritePerm, HasNoteViewPerm, HasAttachmentViewPerm, HasNoteWritePerm


class AttachmentFacadeABC(ABC):
    """Application service for attachment lifecycle."""

    @abstractmethod
    async def post_attachment(self, attachment: Attachment, user_ctx: UserContextABC) -> Attachment:
        """Uploads attachment contents and persists metadata. It does not evaluates permissions, since 
        the attachment is not linked to any note yet."""
        ...

    @abstractmethod
    async def update_metadata(self, attachment: Attachment, user_ctx: UserContextABC) -> Attachment:
        """Searches attachment by key and updates all other fields which are not UNDEFINED. It evaluates write permissions."""
        ...

    @abstractmethod
    async def get_attachment(self, key: str, user_ctx: UserContextABC) -> Attachment:
        """Checks permission and fetches attachment metadata and contents by key."""
        ...

    @abstractmethod
    async def get_metadata(self, key: str, user_ctx: UserContextABC) -> Attachment:
        """Checks permission and fetch attachment metadata without the content payload."""
        ...

    @abstractmethod
    async def delete_attachment(self, key: str, user_ctx: UserContextABC) -> None:
        """Checks permission and deletes attachment metadata and contents."""
        ...

    @abstractmethod
    async def link_attachment_to_note(self, attachment_key: str, note_id: str, user_ctx: UserContextABC) -> None:
        """Checks permission and creates a link between an attachment and a note. This is a separate step to allow linking an attachment to multiple notes."""
        ...
    
    @abstractmethod
    async def unlink_attachment_from_note(self, attachment_key: str, note_id: str, user_ctx: UserContextABC) -> None:
        """Checks permission and removes a link between an attachment and a note."""
        ...

    @abstractmethod
    async def list_attachments_for_note(self, note_id: str, user_ctx: UserContextABC) -> list[Attachment]:
        """Checks permission and lists all attachments linked to a given note."""
        ...


class AttachmentFacade(AttachmentFacadeABC):
    """
    Facade that combines object storage + metadata storage + permission storage.
    It effectively implements the full lifecycle of attachments, containing 
        - upload, download, delete, link to note, unlink from note
    and it evaluates permissions for all operations correctly 
    """

    def __init__(
        self,
        attachment_repo: AttachmentsRepoABC,
        metadata_repo: AttachmentsMetadataRepoABC,
        permission_repo: PermissionRepoABC,

        attachments_note_link_table: TableABC,
        log: LoggingProvider,
        get_now: Callable[[], datetime] = lambda: datetime.now(),
    ) -> None:
        self._permission_repo = permission_repo
        self._attachment_repo = attachment_repo
        self._metadata_repo = metadata_repo
        self._attachments_note_link_table = attachments_note_link_table
        self.log = log(__name__, self)
        self.get_now = get_now

    async def update_metadata(self, attachment: Attachment, user_ctx: UserContextABC) -> Attachment:
        if attachment.key is UNDEFINED:
            raise ValueError("Attachment key must be given perform an update")

        # permission check
        check = HasAttachmentWritePerm(attachment.key).set_permission_repo(self._permission_repo)
        has_permission = await check.check(user_ctx)
        if not has_permission:
            raise has_permission.error
        
        # update metadata
        updated = await self._metadata_repo.update_metadata(attachment, user_ctx)
        return updated
        
    async def post_attachment(self, attachment: Attachment, user_ctx: UserContextABC) -> Attachment:
        # no permission check done - this only uploads. it does not link to any note yet.
        # If not used, it should be cleaned in a task which checks links for an attachment

        if attachment.content is None:
            raise ValueError("Attachment content cannot be empty")

        now = self.get_now()
        if attachment.created_at is UNDEFINED:
            attachment.created_at = now
        if attachment.updated_at is UNDEFINED:
            attachment.updated_at = now

        # send content to object storage
        if attachment.key is UNDEFINED:
            key = await self._attachment_repo.post_attachment(attachment)
            attachment.key = key  # assign generated key back to attachment

        # persist metadata to the database, using key given from object storage
        await self._metadata_repo.post_metadata(attachment, user_ctx)
        self.log.debug(f"Stored attachment metadata for {attachment.key=}")

        return attachment

    async def get_attachment(self, key: str, user_ctx: UserContextABC) -> Attachment:
        # permission check
        check = HasAttachmentViewPerm(key).set_permission_repo(self._permission_repo)
        has_permission = await check.check(user_ctx)
        if not has_permission:
            return PermissionError(f"user {user_ctx.user_id} has no permission to view attachment {key}")
        
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

    async def get_metadata(self, key: str, user_ctx: UserContextABC) -> Attachment:
        # permission check
        check = HasAttachmentViewPerm(key).set_permission_repo(self._permission_repo)
        has_permission = await check.check(user_ctx)
        if not has_permission:
            raise has_permission.error
    
        return await self._metadata_repo.get_metadata(key)

    async def delete_attachment(self, key: str, user_ctx: UserContextABC) -> None:
        # permission check
        check = HasAttachmentWritePerm(key).set_permission_repo(self._permission_repo)
        has_permission = await check.check(user_ctx)
        if not has_permission:
            raise has_permission.error

        # Remove object payload first, then metadata.
        await self._attachment_repo.delete_attachment(key)
        await self._metadata_repo.delete_metadata(key)


    async def link_attachment_to_note(self, attachment_key: str, note_id: str, user_ctx: UserContextABC) -> None:
        # check poermissions
        permission_chain = (
            HasAttachmentViewPerm(attachment_key)
                .set_permission_repo(self._permission_repo)
                .set_next(HasNoteViewPerm(note_id))
        )
        has_permission = await permission_chain.get_first().check(user_ctx)
        if not has_permission:
            raise has_permission.error
        
        # add to attachment_note_link table
        await self._attachments_note_link_table.insert(
            {"note_id": note_id, "attachment_key": attachment_key, "linked_at": self.get_now()}
        )

        # add attachment#parent_note@note to spicedb
        await self._permission_repo.insert([Relationship(
            ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
            AttachmentRelationEnum.PARENT_NOTE,
            SubjectRef(ObjectTypeEnum.NOTE, note_id)
        )])

        return

    async def unlink_attachment_from_note(self, attachment_key: str, note_id: str, user_ctx: UserContextABC) -> None:
        # check permissions
        permission_chain = (
            HasAttachmentViewPerm(attachment_key)
                .set_permission_repo(self._permission_repo)
                .set_next(HasNoteViewPerm(note_id))
        )
        has_permission = await permission_chain.get_first().check(user_ctx)
        if not has_permission:
            raise has_permission.error

        # remove from attachment_note_link table
        await self._attachments_note_link_table.delete(
            {"note_id": note_id, "attachment_key": attachment_key}
        )

        # remove attachment#parent_note@note to spicedb
        await self._permission_repo.delete(Relationship(
            ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
            AttachmentRelationEnum.PARENT_NOTE,
            SubjectRef(ObjectTypeEnum.NOTE, note_id)
        ))

        return

    async def list_attachments_for_note(self, note_id: str, user_ctx: UserContextABC) -> list[Attachment]:
        # permission check
        check = HasNoteViewPerm(note_id).set_permission_repo(self._permission_repo)
        has_permission = await check.check(user_ctx)
        if not has_permission:
            raise has_permission.error
        
        # fetch all attachment links from db and then fetch each attachment from object storage
        links = await self._attachments_note_link_table.select(where={"note_id": note_id})
        attachments = []
        if not links:
            return []
        for link in links:
            attachment = await self.get_attachment(link["attachment_key"], user_ctx=user_ctx)
            attachments.append(attachment)
        return attachments