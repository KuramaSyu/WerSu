"""Gate existing default-fleeting rules on a missing parent directory.

The default ``NoteCreated`` rule used to fire unconditionally.  That
also routed notes that the frontend explicitly created inside a
directory into ``fleeting_notes``.  The rule now carries the
``note_has_no_directory`` condition so it only applies when the note
has no directory assigned.

New rules are created with that condition by the Zettelkasten
bootstrap.  This migration updates the already-stored default rules so
existing users get the same behaviour.
"""

from __future__ import annotations

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


_UPDATE_DEFAULT_FLEETING_RULES_SQL = """
UPDATE rules
SET condition = '{"type": "note_has_no_directory"}'::jsonb,
    updated_at = now()
WHERE event_type = 'NoteCreated'
  AND attached_entity_type = 'shelf'
  AND action_type = 'add_to_directory'
  AND condition IS DISTINCT FROM '{"type": "note_has_no_directory"}'::jsonb;
"""


class Migration(MigrationABC):
    """Rewrite existing default-fleeting rules to the new condition."""

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(_UPDATE_DEFAULT_FLEETING_RULES_SQL)
