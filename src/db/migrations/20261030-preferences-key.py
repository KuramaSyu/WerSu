"""Add an optional key column to the unified preferences table.

A preference row is now addressed by ``(scope, target_id, key)``
instead of ``(scope, target_id)``.  ``key`` defaults to the empty
string so the legacy scope+target-only call shape keeps addressing
exactly the same row it did before.
"""

from __future__ import annotations

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


_ADD_KEY_SQL = """
ALTER TABLE preferences
    ADD COLUMN IF NOT EXISTS key TEXT NOT NULL DEFAULT '';
"""


# The old per-owner indexes were partial unique indexes on the owner
# column alone.  Replace them with composite (owner, key) indexes so
# one owner can hold many keyed rows, while still keeping the empty
# key as the single legacy/default row.
_REINDEX_SQL = """
DROP INDEX IF EXISTS preferences_user_uidx;
DROP INDEX IF EXISTS preferences_shelf_uidx;
DROP INDEX IF EXISTS preferences_role_uidx;

CREATE UNIQUE INDEX IF NOT EXISTS preferences_user_key_uidx
    ON preferences (user_id, key) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS preferences_shelf_key_uidx
    ON preferences (shelf_id, key) WHERE shelf_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS preferences_role_key_uidx
    ON preferences (role_id, key) WHERE role_id IS NOT NULL;
"""


class Migration(MigrationABC):
    """Add preferences.key and re-key the unique indexes on (owner, key)."""

    async def up(self, ctx: MigrationContext) -> None:
        """Add the key column, then swap the per-owner unique indexes."""
        await ctx.db.execute(_ADD_KEY_SQL)
        await ctx.db.execute(_REINDEX_SQL)
