"""Concrete :class:`~src.api.services.directory_activity_service.DirectoryActivityServiceABC` implementation."""

from __future__ import annotations

from typing import List, Optional

from src.api import LoggingProvider
from src.api.other.user_context import UserContextABC
from src.api.services.directory_activity_service import DirectoryActivityServiceABC
from src.db.entities.note.versioning import NoteVersionEntry
from src.db.repos.directory.directory import DirectoryFacadeABC
from src.db.repos.note.versioning import NoteVersionRepoABC


class DirectoryActivityServiceImpl(DirectoryActivityServiceABC):
    """Resolve directory-scoped activity using note version history."""

    def __init__(
        self,
        version_repo: NoteVersionRepoABC,
        directory_repo: DirectoryFacadeABC,
        log: LoggingProvider,
    ) -> None:
        self._version_repo = version_repo
        self._directory_repo = directory_repo
        self.log = log(__name__, self)

    async def list_directory_activity(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
        limit: int = 25,
        offset: int = 0,
    ) -> List[NoteVersionEntry]:
        if limit < 0:
            raise ValueError("limit must be >= 0")
        if offset < 0:
            raise ValueError("offset must be >= 0")

        note_ids = await self._directory_repo.resolve_files_of_directory(
            directory_id=directory_id,
            actor=actor,
            max_depth=max_depth,
        )
        if not note_ids:
            return []

        entries: List[NoteVersionEntry] = []
        for note_id in note_ids:
            versions = await self._version_repo.list_versions(
                note_id, limit=1, offset=0
            )
            if versions:
                entries.append(versions[0])

        entries.sort(key=lambda entry: entry.created_at, reverse=True)
        if offset >= len(entries):
            return []
        return entries[offset : offset + limit]


__all__ = ["DirectoryActivityServiceImpl"]