from __future__ import annotations

from typing import List, Optional, Tuple
from src.api.services.note_service import NoteServiceABC
from src.api.other.user_context import UserContextABC
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.attachments.attachments import Attachment
from src.services.attachment_facade import AttachmentFacadeABC


class _StubBookstackEmergencyNoteService(NoteServiceABC):
    def __init__(self) -> None:
        self.inserted: List[NoteEntity] = []
        self._next_id = 0

    async def insert_note(self, note: NoteEntity, user_ctx: UserContextABC) -> NoteEntity:
        self._next_id += 1
        inserted = NoteEntity(
            note_id=f"note-{self._next_id}",
            title=note.title,
            content=note.content,
            author_id=note.author_id,
            directory_ids=list(note.directory_ids or []),
            permissions=[],
        )
        self.inserted.append(inserted)
        return inserted

    async def update_note(self, note: NoteEntity, user_ctx: UserContextABC) -> NoteEntity:
        return note

    async def get_note(self, note_id, user_ctx, *, include=None):
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):
        raise NotImplementedError

    async def search_notes(self, search_type, query, user_ctx, limit, offset):
        raise NotImplementedError

    async def get_notes(self, note_ids, user_ctx, options=None):
        raise NotImplementedError


class _StubBookstackEmergencyAttachmentFacade(AttachmentFacadeABC):
    def __init__(self) -> None:
        self.posted: List[Attachment] = []

    async def post_attachment(self, attachment: Attachment, user_ctx: UserContextABC) -> Attachment:
        self.posted.append(attachment)
        return Attachment(
            key=f"att-{attachment.filename}",
            filename=attachment.filename,
            filepath=attachment.filepath,
            content_type=attachment.content_type,
            size=attachment.size,
        )

    async def update_metadata(self, attachment, user_ctx):
        raise NotImplementedError

    async def get_attachment(self, key, user_ctx):
        raise NotImplementedError

    async def get_metadata(self, key, user_ctx):
        raise NotImplementedError

    async def delete_attachment(self, key, user_ctx):
        raise NotImplementedError

    async def link_attachment(self, attachment_key, sub_type, sub_id, user_ctx):
        return None

    async def unlink_attachment(self, attachment_key, sub_type, sub_id, user_ctx):
        raise NotImplementedError

    async def list_attachments(self, sub_type, sub_id, user_ctx):
        raise NotImplementedError

    async def get_attachment_token(self, attachment_key, user_ctx):
        raise NotImplementedError


__all__ = [
    '_StubBookstackEmergencyNoteService',
    '_StubBookstackEmergencyAttachmentFacade',
]
