"""Index note-version delta timestamps for daily compaction.

The daily compaction process scans ``note.version_delta`` by
``created_at`` (no note id), which the existing ``(note_id,
version_index)`` indexes cannot serve.  Without this index the nightly
sweep is a full table scan.
"""

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Create a ``created_at`` index on ``note.version_delta``."""

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(
            """
            CREATE INDEX IF NOT EXISTS note_version_delta_created_at_idx
            ON note.version_delta (created_at);
            """
        )
