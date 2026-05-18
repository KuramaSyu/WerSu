from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Add note version snapshot and delta tables."""

    async def up(self, ctx: MigrationContext) -> None:
        """Create snapshot + delta tables for note content versioning."""
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS note.version_snapshot (
                snapshot_id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                note_id TEXT NOT NULL REFERENCES note.content(id) ON DELETE CASCADE ON UPDATE CASCADE,
                version_index BIGINT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                author_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
                title TEXT,
                content TEXT NOT NULL,
                UNIQUE (note_id, version_index)
            );

            CREATE TABLE IF NOT EXISTS note.version_delta (
                delta_id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                note_id TEXT NOT NULL REFERENCES note.content(id) ON DELETE CASCADE ON UPDATE CASCADE,
                snapshot_id TEXT NOT NULL REFERENCES note.version_snapshot(snapshot_id) ON DELETE CASCADE ON UPDATE CASCADE,
                version_index BIGINT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                author_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
                title_patch TEXT,
                content_patch TEXT NOT NULL,
                UNIQUE (note_id, version_index)
            );

            CREATE INDEX IF NOT EXISTS note_version_snapshot_note_idx
            ON note.version_snapshot (note_id, version_index DESC);

            CREATE INDEX IF NOT EXISTS note_version_delta_note_idx
            ON note.version_delta (note_id, version_index DESC);

            CREATE INDEX IF NOT EXISTS note_version_delta_snapshot_idx
            ON note.version_delta (snapshot_id, version_index DESC);
            """
        )
