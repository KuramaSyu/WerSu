from __future__ import annotations

from src.api.other.user_context import UserContextABC
from src.api.services.note_service import NoteServiceABC
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.attachments.attachments import Attachment
from src.services.attachment_facade import AttachmentFacadeABC
from typing import Dict, List, Optional, Tuple


class _StubBookstackNoteService(NoteServiceABC):
    """Records every note call; auto-assigns sequential ids."""

    def __init__(self) -> None:
        self.inserted: List[NoteEntity] = []
        self.updated: List[NoteEntity] = []
        self.insert_calls: List[Tuple[NoteEntity, str]] = []
        self.update_calls: List[Tuple[NoteEntity, str]] = []
        self._next_id = 0
        self.insert_should_raise: Optional[Exception] = None

    async def insert_note(self, note: NoteEntity, user_ctx: UserContextABC) -> NoteEntity:
        self.insert_calls.append((note, user_ctx.user_id))
        if self.insert_should_raise is not None:
            raise self.insert_should_raise
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
        self.update_calls.append((note, user_ctx.user_id))
        self.updated.append(note)
        return note

    async def get_note(self, note_id, user_ctx, *, include=None):
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):
        raise NotImplementedError

    async def search_notes(self, search_type, query, user_ctx, limit, offset):
        raise NotImplementedError

    async def get_notes(self, note_ids, user_ctx, options=None):
        raise NotImplementedError


class _StubBookstackAttachmentFacade(AttachmentFacadeABC):
    """Records every attachment call; assigns sequential keys."""

    def __init__(self) -> None:
        self.posted: List[Attachment] = []
        self.links: List[Tuple[str, str, str]] = []  # (key, note_id, user_id)
        self._next_key = 0
        self.post_should_raise: Optional[Exception] = None
        self.link_should_raise: Optional[Exception] = None

    async def post_attachment(
        self, attachment: Attachment, user_ctx: UserContextABC
    ) -> Attachment:
        self._next_key += 1
        key = f"att-{self._next_key}"
        stored = Attachment(
            key=key,
            filename=attachment.filename,
            filepath=attachment.filepath,
            content_type=attachment.content_type,
            size=attachment.size,
            content=attachment.content,
            created_at=attachment.created_at,
            updated_at=attachment.updated_at,
        )
        self.posted.append(stored)
        if self.post_should_raise is not None:
            raise self.post_should_raise
        return stored

    async def update_metadata(self, attachment, user_ctx):
        raise NotImplementedError

    async def get_attachment(self, key, user_ctx):
        raise NotImplementedError

    async def get_metadata(self, key, user_ctx):
        raise NotImplementedError

    async def delete_attachment(self, key, user_ctx):
        raise NotImplementedError

    async def link_attachment(
        self, attachment_key: str, sub_type, sub_id, user_ctx: UserContextABC
    ) -> None:
        self.links.append((attachment_key, sub_type, sub_id, user_ctx.user_id))
        if self.link_should_raise is not None:
            raise self.link_should_raise

    async def unlink_attachment(
        self, attachment_key, sub_type, sub_id, user_ctx
    ):
        raise NotImplementedError

    async def list_attachments(self, sub_type, sub_id, user_ctx):
        raise NotImplementedError

    async def get_attachment_token(self, attachment_key, user_ctx):
        raise NotImplementedError


__all__ = [
    '_StubBookstackNoteService',
    '_StubBookstackAttachmentFacade',
]
