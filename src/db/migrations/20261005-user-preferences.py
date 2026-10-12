"""Add the unified preferences table."""

from __future__ import annotations

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


_PREFS_SQL = """
CREATE TABLE IF NOT EXISTS preferences (
    id          TEXT PRIMARY KEY DEFAULT uuidv7()::text,
    user_id     TEXT NULL
        REFERENCES auth.user(id) ON DELETE CASCADE ON UPDATE CASCADE,
    shelf_id    uuid NULL
        REFERENCES note.shelf(id) ON DELETE CASCADE ON UPDATE CASCADE,
    role_id     TEXT NULL
        REFERENCES roles(id) ON DELETE CASCADE ON UPDATE CASCADE,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT preferences_one_owner CHECK (
        (CASE WHEN user_id  IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN shelf_id IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN role_id  IS NOT NULL THEN 1 ELSE 0 END) = 1
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS preferences_user_uidx
    ON preferences (user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS preferences_shelf_uidx
    ON preferences (shelf_id) WHERE shelf_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS preferences_role_uidx
    ON preferences (role_id) WHERE role_id IS NOT NULL;
"""


class Migration(MigrationABC):
    """Create the preferences table with three cascading owner FKs."""

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(_PREFS_SQL)
