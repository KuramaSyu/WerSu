"""Add the attachment <-> user link table.

Adds ``note.attachment_user_link`` mirroring the existing
``note.attachment_note_link``.  Both link tables share the same
``note.attachment`` row they point at, but only one of them
represents the active owning relation.  Cascade behaviour matches
``attachment_note_link``: deleting either side removes the link row.
"""

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Create the attachment-user link table."""

    async def up(self, ctx: MigrationContext) -> None:
        """Create ``note.attachment_user_link``.

        Mirrors ``note.attachment_note_link`` but references the
        ``users`` table instead of ``note.content``.  Used by
        :class:`src.services.attachment_facade.AttachmentFacadeImpl`
        when :meth:`~src.api.services.attachment_facade.AttachmentFacadeABC.link_attachment`
        is called with ``sub_type == "user"``.
        """
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS note.attachment_user_link (
                user_id TEXT NOT NULL,
                attachment_key TEXT NOT NULL,

                linked_at TIMESTAMP NOT NULL,

                PRIMARY KEY (user_id, attachment_key),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
                FOREIGN KEY (attachment_key) REFERENCES note.attachment(key) ON DELETE CASCADE ON UPDATE CASCADE
            );
            """
        )
