"""Concrete :class:`~src.api.services.attachment_facade.AttachmentFacadeABC` implementation.

Facade that combines object storage
(:class:`src.db.repos.attachments.attachments.AttachmentS3Repo`),
metadata storage
(:class:`src.db.repos.attachments.attachments.AttachmentMetadataPostgresRepo`)
and the permission backend
(:class:`src.db.repos.permissions.spicedb_repo.SpicedbPermissionRepo`).

It implements the full attachment lifecycle (upload, download,
delete, link to a note, unlink from a note) and enforces the right
permission for every operation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from src.api import (
    AttachmentRelationEnum,
    LoggingProvider,
    ObjectRef,
    ObjectTypeEnum,
    PermissionRepoABC,
    Relationship,
    SubjectRef,
    UNDEFINED,
    UserContextABC,
)
from src.api.services.attachment_facade import AttachmentFacadeABC
from src.db.repos.attachments.attachments import (
    Attachment,
    AttachmentMetadataRepoABC,
    AttachmentRepoABC,
)
from src.db.table import TableABC
from src.domain.permission_chain import (
    HasAttachmentViewPerm,
    HasAttachmentWritePerm,
    HasNoteViewPerm,
    HasNoteWritePerm,
)


class AttachmentFacadeImpl(AttachmentFacadeABC):
    """Composition of object storage + metadata repo + permission repo."""

    def __init__(
        self,
        attachment_repo: AttachmentRepoABC,
        metadata_repo: AttachmentMetadataRepoABC,
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

    async def update_metadata(
        self,
        attachment: Attachment,
        user_ctx: UserContextABC,
    ) -> Attachment:
        if attachment.key is UNDEFINED:
            raise ValueError("Attachment key must be given perform an update")

        check = HasAttachmentWritePerm(attachment.key).set_permission_repo(
            self._permission_repo
        )
        has_permission = await check.check(user_ctx)
        if has_permission.error:
            raise has_permission.error

        updated = await self._metadata_repo.update_metadata(attachment, user_ctx)
        return updated

    async def post_attachment(
        self,
        attachment: Attachment,
        user_ctx: UserContextABC,
    ) -> Attachment:
        if attachment.content is None:
            raise ValueError("Attachment content cannot be empty")

        now = self.get_now()
        if attachment.created_at is UNDEFINED:
            attachment.created_at = now
        if attachment.updated_at is UNDEFINED:
            attachment.updated_at = now
        if attachment.filepath is UNDEFINED:
            raise ValueError(
                "Attachment filepath must be given. Try to provide it as "
                "/directory/path/filename.ext for debug purposes or later conversion"
            )

        if attachment.key is UNDEFINED:
            key = await self._attachment_repo.post_attachment(attachment)
            attachment.key = key

        await self._metadata_repo.post_metadata(attachment, user_ctx)
        self.log.debug(f"Stored attachment metadata for {attachment.key=}")

        return attachment

    async def get_attachment(self, key: str, user_ctx: UserContextABC) -> Attachment:
        check = HasAttachmentViewPerm(key).set_permission_repo(self._permission_repo)
        has_permission = await check.check(user_ctx)
        if has_permission.error:
            raise has_permission.error

        metadata = await self._metadata_repo.get_metadata(key)
        content_attachment = await self._attachment_repo.get_attachment(key)

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
        check = HasAttachmentViewPerm(key).set_permission_repo(self._permission_repo)
        has_permission = await check.check(user_ctx)
        if has_permission.error:
            raise has_permission.error

        return await self._metadata_repo.get_metadata(key)

    async def delete_attachment(self, key: str, user_ctx: UserContextABC) -> None:
        check = HasAttachmentWritePerm(key).set_permission_repo(self._permission_repo)
        has_permission = await check.check(user_ctx)
        if has_permission.error:
            raise has_permission.error

        await self._attachment_repo.delete_attachment(key)
        await self._metadata_repo.delete_metadata(key)

        await self._permission_repo.delete(
            Relationship(
                ObjectRef(ObjectTypeEnum.ATTACHMENT, key),
                relation=UNDEFINED,
                subject=SubjectRef(UNDEFINED, UNDEFINED),
            )
        )

    async def link_attachment_to_note(
        self,
        attachment_key: str,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        permission_chain = (
            HasAttachmentViewPerm(attachment_key)
            .set_permission_repo(self._permission_repo)
            .set_next(HasNoteViewPerm(note_id))
        )
        has_permission = await permission_chain.get_first().check(user_ctx)
        if not has_permission:
            raise has_permission.error

        await self._attachments_note_link_table.insert(
            {
                "note_id": note_id,
                "attachment_key": attachment_key,
                "linked_at": self.get_now(),
            }
        )

        await self._permission_repo.insert(
            [
                Relationship(
                    ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                    AttachmentRelationEnum.PARENT_NOTE,
                    SubjectRef(ObjectTypeEnum.NOTE, note_id),
                )
            ]
        )

    async def unlink_attachment_from_note(
        self,
        attachment_key: str,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        permission_chain = (
            HasAttachmentViewPerm(attachment_key)
            .set_permission_repo(self._permission_repo)
            .set_next(HasNoteViewPerm(note_id))
        )
        has_permission = await permission_chain.get_first().check(user_ctx)
        if not has_permission:
            raise has_permission.error

        await self._attachments_note_link_table.delete(
            {"note_id": note_id, "attachment_key": attachment_key}
        )

        await self._permission_repo.delete(
            Relationship(
                ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                AttachmentRelationEnum.PARENT_NOTE,
                SubjectRef(ObjectTypeEnum.NOTE, note_id),
            )
        )

    async def list_attachments_for_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> list[Attachment]:
        check = HasNoteViewPerm(note_id).set_permission_repo(self._permission_repo)
        has_permission = await check.check(user_ctx)
        if not has_permission:
            raise has_permission.error

        links = await self._attachments_note_link_table.select(where={"note_id": note_id})
        attachments: list[Attachment] = []
        if not links:
            return []
        for link in links:
            attachment = await self.get_attachment(
                link["attachment_key"], user_ctx=user_ctx
            )
            attachments.append(attachment)
        return attachments


__all__ = ["AttachmentFacadeImpl"]