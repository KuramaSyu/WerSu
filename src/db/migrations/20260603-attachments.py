from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Create attachments metadata table."""

    async def up(self, ctx: MigrationContext) -> None:
        """Create the attachment metadata table used by the facade."""
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS note.attachment (
                key TEXT PRIMARY KEY,  -- S3 key

                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size BIGINT NOT NULL,
                
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                created_by TEXT NOT NULL REFERENCES users(id) ON DELETE SET NULL ON UPDATE CASCADE,  -- don't delete attachments if user is deleted -> other users maybe need them
                
                sha256 TEXT NOT NULL,  -- for deduplication
                
                metadata JSONB  -- for extensibility
            );

            CREATE INDEX IF NOT EXISTS note_attachment_sha256_idx
            ON note.attachment (sha256);

            CREATE TABLE IF NOT EXISTS note.attachment_note_link (
                note_id TEXT NOT NULL,
                attachment_key TEXT NOT NULL,

                linked_at TIMESTAMP NOT NULL,

                PRIMARY KEY (note_id, attachment_key),
                FOREIGN KEY (note_id) REFERENCES note.content(id) ON DELETE CASCADE ON UPDATE CASCADE,
                FOREIGN KEY (attachment_key) REFERENCES note.attachment(key) ON DELETE CASCADE ON UPDATE CASCADE
            );
            """
        )
