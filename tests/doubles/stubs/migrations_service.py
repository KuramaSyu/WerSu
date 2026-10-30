from __future__ import annotations

from typing import AsyncIterator, List, Optional, cast
from src.api.other.user_context import UserContextABC
from src.services.thirdparty_migrations import (
    ImportedChapter,
    MigrationResult,
    ThirdpartyMigrationsServiceABC,
)


class _StubMigrationsService(ThirdpartyMigrationsServiceABC):
    """Records the call; returns a preset :class:`MigrationResult`."""

    def __init__(self, result: Optional[MigrationResult] = None) -> None:
        self._result = result or MigrationResult(
            root_directory_id="root-1",
            pages_imported=3,
            attachments_uploaded=5,
            chapters=[
                ImportedChapter(
                    directory_id="chap-1",
                    chapter_name="Chapter 1",
                    pages_imported=2,
                ),
                ImportedChapter(
                    directory_id="chap-2",
                    chapter_name="Chapter 2",
                    pages_imported=1,
                ),
            ],
        )
        self.last_content: Optional[bytes] = None
        self.last_user_id: Optional[str] = None
        self.migrate_should_raise: Optional[Exception] = None

    async def migrate(
        self, content: bytes, user_ctx: UserContextABC
    ) -> MigrationResult:
        self.last_content = content
        self.last_user_id = user_ctx.user_id
        if self.migrate_should_raise is not None:
            raise self.migrate_should_raise
        return self._result


__all__ = [
    '_StubMigrationsService',
]
