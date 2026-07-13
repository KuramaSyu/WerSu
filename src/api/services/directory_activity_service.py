"""Abstract service for directory-scoped activity streams.

Implementations:
* :class:`src.services.directory_activity_service.DirectoryActivityServiceImpl`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.other.user_context import UserContextABC
from src.db.entities.note.versioning import NoteVersionEntry


class DirectoryActivityServiceABC(ABC):
    """Facade for directory-based note version activity.

    Implementations:
    * :class:`src.services.directory_activity_service.DirectoryActivityServiceImpl`
    """

    @abstractmethod
    async def list_directory_activity(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
        limit: int = 25,
        offset: int = 0,
    ) -> List[NoteVersionEntry]:
        """Return the most recent version entry per note in a directory tree.

        Args:
            directory_id: root directory to scan; ``None`` resolves
                to the actor's visible top-level directories.
            actor: caller identity (used for permission checks).
            max_depth: how deep into the directory tree to scan.
            limit: page size.
            offset: page offset.

        Raises:
            ValueError: when ``limit`` or ``offset`` is negative.

        Returns:
            List[NoteVersionEntry]: one entry per note that has at
            least one version, newest first.
        """
        ...


__all__ = ["DirectoryActivityServiceABC"]