from __future__ import annotations

from typing import List, Optional, cast
from src.db.entities.note.versioning import NoteVersionContent, NoteVersionEntry
from src.services.directory_activity_service import DirectoryActivityServiceABC


class _FakeSpyDirectoryActivityService(DirectoryActivityServiceABC):
    async def list_directory_activity(
        self,
        directory_id: Optional[str],
        actor: UserContext,
        max_depth: int = 10,
        limit: int = 25,
        offset: int = 0,
    ) -> List[NoteVersionEntry]:
        return []


__all__ = [
    '_FakeSpyDirectoryActivityService',
]
