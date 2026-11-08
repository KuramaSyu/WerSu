"""Add the ``calculated_stats`` table.

One row per user holds the precomputed note / character / directory
counts produced by the daily calculated-stats background process.  The
row is keyed by ``user_id``, cascades with the user row, and keeps the
last recalculation timestamp for operators.
"""

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Create ``calculated_stats`` and its timestamp index."""

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS calculated_stats (
                user_id TEXT PRIMARY KEY
                    REFERENCES auth.user(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE,
                total_chars BIGINT NOT NULL DEFAULT 0,
                total_notes BIGINT NOT NULL DEFAULT 0,
                total_chapters BIGINT NOT NULL DEFAULT 0,
                total_books BIGINT NOT NULL DEFAULT 0,
                calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS calculated_stats_calculated_at_idx
                ON calculated_stats (calculated_at);
            """
        )
