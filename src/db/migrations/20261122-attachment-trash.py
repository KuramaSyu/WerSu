"""Add the attachment trash marker.

Unlinked attachments are not hard-deleted immediately: they are moved
to a trash state by stamping ``trashed_at``.  The column is ``NULL``
for live attachments and holds the time the attachment was trashed
otherwise.  Retaining the row keeps the S3 key, ``sha256`` and
permission tuples intact so a re-link inside the retention window can
restore the attachment with a single ``UPDATE``.

The partial index keeps the cleanup scan (``trashed_at IS NULL`` +
link ``NOT EXISTS``) and the purge scan (``trashed_at < cutoff``)
cheap even when the table grows.
"""

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Add ``trashed_at`` to ``note.attachment`` and index it."""

    async def up(self, ctx: MigrationContext) -> None:
        """Add the trash timestamp column if it does not exist yet."""
        await ctx.db.execute(
            """
            ALTER TABLE note.attachment
                ADD COLUMN IF NOT EXISTS trashed_at TIMESTAMP;

            CREATE INDEX IF NOT EXISTS note_attachment_trashed_at_idx
            ON note.attachment (trashed_at);
            """
        )
