from __future__ import annotations

from typing import Optional, cast
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import UserContextABC
from src.api.services.note_service import NoteIncludeOptions, NoteResponse, NoteServiceABC


class _FakeSpyGrpcNoteService(NoteServiceABC):
    """`NoteServiceABC` stub that returns a preset :class:`NoteResponse`."""

    def __init__(self, response: NoteResponse) -> None:
        self._response = response
        self.last_note_id: Optional[str] = None
        self.last_user_ctx: Optional[UserContextABC] = None

    async def get_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
        *,
        include: Optional[NoteIncludeOptions] = None,
    ) -> NoteResponse:
        self.last_note_id = note_id
        self.last_user_ctx = user_ctx
        self.last_include = include
        return self._response

    async def insert_note(self, note, user_ctx):  # pragma: no cover - unused
        raise NotImplementedError

    async def update_note(self, note, user_ctx):  # pragma: no cover - unused
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):  # pragma: no cover - unused
        raise NotImplementedError

    async def search_notes(
        self,
        search_type: str,
        query: str,
        user_ctx: UserContextABC,
        limit: int,
        offset: int,
    ):  # pragma: no cover - unused
        raise NotImplementedError

    async def get_notes(
        self,
        note_ids,
        user_ctx,
        options=None,
    ):  # pragma: no cover - unused
        raise NotImplementedError


class _FakeSpyCapturingNoteService(NoteServiceABC):
    """Records the entity handed to `insert_note` and echoes a fake id."""

    def __init__(self) -> None:
        self.last_note = None
        self.last_user_ctx = None

    async def insert_note(self, note, user_ctx):
        self.last_note = note
        self.last_user_ctx = user_ctx
        # the repo would normally assign an id; fake one so the
        # grpc visitor can serialize the response without raising.
        if note.note_id is None or note.note_id is UNDEFINED:
            note.note_id = "note-fake-id"
        return note

    async def update_note(self, note, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def get_note(self, note_id, user_ctx, *, include=None):  # pragma: no cover
        raise NotImplementedError

    async def search_notes(  # pragma: no cover
        self, search_type, query, user_ctx, limit, offset,
    ):
        raise NotImplementedError

    async def get_notes(self, note_ids, user_ctx, options=None):  # pragma: no cover
        raise NotImplementedError


class _FakeSpyUpdateNoteService(NoteServiceABC):
    """`NoteServiceABC` stub that records the entity handed to `update_note`."""

    def __init__(self) -> None:
        self.last_note = None
        self.last_user_ctx = None

    async def update_note(self, note, user_ctx):
        self.last_note = note
        self.last_user_ctx = user_ctx
        return note

    async def insert_note(self, note, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def get_note(self, note_id, user_ctx, *, include=None):  # pragma: no cover
        raise NotImplementedError

    async def search_notes(  # pragma: no cover
        self, search_type, query, user_ctx, limit, offset,
    ):
        raise NotImplementedError

    async def get_notes(self, note_ids, user_ctx, options=None):  # pragma: no cover
        raise NotImplementedError


__all__ = [
    '_FakeSpyGrpcNoteService',
    '_FakeSpyCapturingNoteService',
    '_FakeSpyUpdateNoteService',
]
