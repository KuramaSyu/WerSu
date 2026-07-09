"""Application service for importing content from third-party tools.

The :class:`ThirdpartyMigrationsServiceABC` exposes one method,
:meth:`migrate`, that takes the raw bytes of an export zip (whatever
the source format) and returns a :class:`MigrationResult`.  Each
implementation handles one source (BookStack today; Notion, Confluence,
Obsidian, etc. tomorrow) and is responsible for translating the
source format into the project's directory / note / attachment model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

from src.api import UserContextABC


@dataclass
class ImportedChapter:
    """Summary of one chapter directory that was created."""

    directory_id: str
    chapter_name: str
    pages_imported: int


@dataclass
class MigrationResult:
    """Summary of a successful migration.

    Returned by every :class:`ThirdpartyMigrationsServiceABC.migrate`
    call.  The shape is intentionally generic -- future sources may
    leave `chapters` empty when the source has no chapter concept
    (Notion pages, Obsidian folders, ...).
    """

    root_directory_id: str
    pages_imported: int
    attachments_uploaded: int
    chapters: List[ImportedChapter] = field(default_factory=list)


class ThirdpartyMigrationsServiceABC(ABC):
    """Application service for importing third-party exports.

    Implementations:
        - :class:`~src.services.thirdparty_migrations.bookstack.BookstackBookImport`
          imports a BookStack portable book zip.
    """

    @abstractmethod
    async def migrate(
        self,
        content: bytes,
        user_ctx: UserContextABC,
    ) -> MigrationResult:
        """Import `content` into the project on behalf of `user_ctx`.

        Args:
            content: the raw bytes of the export zip (full or last
                chunk after streaming reassembly).
            user_ctx: the calling user; used for every permission
                check and as the author of any created notes /
                admin of any created directories.

        Returns:
            :class:`MigrationResult` summarising what was created.

        Raises:
            ValueError: the content cannot be parsed as the expected
                export format (e.g. not a valid BookStack zip).
            PermissionError: the user lacks permission to perform the
                migration (e.g. cannot create notes or directories).
        """
        ...


__all__ = [
    "ImportedChapter",
    "MigrationResult",
    "ThirdpartyMigrationsServiceABC",
]