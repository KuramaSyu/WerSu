from __future__ import annotations

from datetime import datetime
from typing import List, Optional, cast
from src.api.other.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.versioning import NoteVersionContent, NoteVersionEntry
from src.api.facades.note_facade import NoteFacadeABC
from src.db.repos.note.versioning import NoteVersionRepoABC


class _StubGrpcNoteVersionRepo(NoteVersionRepoABC):
    def __init__(self) -> None:
        self.entries = [
            NoteVersionEntry(
                version_id="v-1",
                note_id="note-1",
                version_index=1,
                created_at=datetime(2026, 5, 18, 9, 0, 0),
                author_id="user-1",
                is_snapshot=True,
                snapshot_id="snap-1",
            )
        ]
        self.content = NoteVersionContent(
            note_id="note-1",
            version_index=1,
            created_at=datetime(2026, 5, 18, 9, 0, 0),
            author_id="user-1",
            title="Title",
            content="Content",
        )

    @property
    def max_deltas_per_snapshot(self) -> int:
        return 2

    async def record_initial_snapshot(self, note_id, title, content, author_id, created_at):
        raise NotImplementedError()

    async def append_version(self, note_id, old_title, old_content, new_title, new_content, author_id, created_at):
        raise NotImplementedError()

    async def list_versions(self, note_id: str, limit: int, offset: int) -> List[NoteVersionEntry]:
        return self.entries

    async def get_content_at_version(self, note_id: str, version_index: int) -> NoteVersionContent:
        return self.content


class _StubGrpcNoteRepo(NoteFacadeABC):
    def __init__(self) -> None:
        self.last_updated: Optional[NoteEntity] = None

    async def insert(self, note: NoteEntity, user: UserContext):
        raise NotImplementedError()

    async def update(self, note: NoteEntity, ctx: UserContext):
        if note.permissions is UNDEFINED:
            note.permissions = []
        self.last_updated = note
        return note

    async def delete(self, note_id: str, ctx: UserContext):
        raise NotImplementedError()

    async def select_by_id(self, note_id: str, ctx: UserContext):
        raise NotImplementedError()

    async def select_by_ids(self, note_ids, ctx: UserContext):
        raise NotImplementedError()

    async def search_notes(self, search_type, query: str, ctx: UserContext, pagination):
        raise NotImplementedError()


__all__ = [
    '_StubGrpcNoteVersionRepo',
    '_StubGrpcNoteRepo',
]
