from __future__ import annotations

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


_DEDUP_SQL = """
-- Keep the oldest row per non-null email; the rest are duplicates
-- of the same address and have to go before we can attach a
-- unique index.  uuidv7 is time-sortable so MIN(id) = oldest.
DELETE FROM auth.user a
USING auth.user b
WHERE a.id > b.id
  AND a.email IS NOT NULL
  AND b.email IS NOT NULL
  AND a.email = b.email;

-- Same idea for username: one row per non-null username.
DELETE FROM auth.user a
USING auth.user b
WHERE a.id > b.id
  AND a.username IS NOT NULL
  AND b.username IS NOT NULL
  AND a.username = b.username;
"""

_UNIQUE_CONSTRAINTS_SQL = """
-- Partial unique index: NULL emails are allowed (OAuth-only
-- signups can have no address yet), but two non-null emails
-- cannot collide.
CREATE UNIQUE INDEX IF NOT EXISTS auth_user_email_uidx
    ON auth.user (email)
    WHERE email IS NOT NULL;

-- Same shape for username: NULL usernames are allowed, but
-- two non-null usernames cannot collide.
CREATE UNIQUE INDEX IF NOT EXISTS auth_user_username_uidx
    ON auth.user (username)
    WHERE username IS NOT NULL;
"""

_DISPLAY_NAME_SQL = """
ALTER TABLE auth.user
    ADD COLUMN IF NOT EXISTS display_name TEXT NULL;
"""


class Migration(MigrationABC):
    """Dedupe auth.user, add unique email/username + optional display_name."""

    async def up(self, ctx: MigrationContext) -> None:
        """Dedupe, then add the unique indexes and the display_name column."""
        await ctx.db.execute(_DEDUP_SQL)
        await ctx.db.execute(_DISPLAY_NAME_SQL)
        await ctx.db.execute(_UNIQUE_CONSTRAINTS_SQL)
