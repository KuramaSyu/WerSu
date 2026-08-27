"""Backfill the per-user ``users_shelf`` for users created before shelves.

The :mod:`20260825-add-shelf` migration added the shelf tables but
deliberately stopped short of backfilling existing users, so the new
``NoteFacadeImpl._resolve_directory_ids`` (which looks up a
default-fleeting rule per user) would raise for every existing note.

This migration closes the gap:

1. Creates a ``users_shelf`` row per user in ``note.shelf``.
2. Binds every existing directory to that shelf.
3. Inserts a default ``NoteCreated`` rule pointing at the user's
   fleeting book (only when a fleeting book exists).

Idempotent: re-runs find the existing shelf + rule and no-op.
"""

from __future__ import annotations

import json

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext
from src.services.user_service import (
    USERS_SHELF_DESCRIPTION,
    users_shelf_display_name_for,
    users_shelf_slug_for,
)


class Migration(MigrationABC):
    """Backfill users_shelf + shelf_book + default-fleeting rule."""

    async def up(self, ctx: MigrationContext) -> None:
        """Run the backfill in one pass."""
        # Discover users
        rows = await ctx.db.fetch(
            "SELECT u.id AS user_id, u.username FROM auth.user u ORDER BY u.id"
        )
        if not rows:
            return
        users = [
            (str(r.get("user_id")), r.get("username"))
            for r in rows
            if r.get("user_id") is not None
        ]
        await self._bootstrap_per_user_shelves(ctx, users)
        await self._attach_default_fleeting_rules(ctx, users)

    async def _bootstrap_per_user_shelves(
        self,
        ctx: MigrationContext,
        users: list[tuple[str, object]],
    ) -> None:
        """Create one shelf per user, slug ``<username>'s shelf``."""
        for user_id, username in users:
            shelf_slug = users_shelf_slug_for(username)
            # Skip when the shelf already exists for this slug.
            existing = await ctx.db.fetchrow(
                "SELECT id FROM note.shelf WHERE slug = $1",
                shelf_slug,
            )
            if existing and existing.get("id") is not None:
                continue
            shelf_row = await ctx.db.fetchrow(
                """
                INSERT INTO note.shelf (
                    slug, display_name, description
                ) VALUES ($1, $2, $3)
                RETURNING id
                """,
                shelf_slug,
                users_shelf_display_name_for(username),
                USERS_SHELF_DESCRIPTION,
            )
            if not shelf_row:
                continue
            shelf_id = str(shelf_row.get("id"))
            # Bind every directory.  Per-user ownership is not
            # recorded in Postgres (lives in SpiceDB), so for
            # single-tenant deployments this binds the whole
            # directory set; multi-tenant backfill is out of
            # scope here.
            dir_ids = await ctx.db.fetch("SELECT id FROM note.directory")
            for d in dir_ids:
                did = d.get("id")
                if did is None:
                    continue
                await ctx.db.execute(
                    """
                    INSERT INTO note.shelf_book (shelf_id, book_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    shelf_id,
                    str(did),
                )

    async def _attach_default_fleeting_rules(
        self,
        ctx: MigrationContext,
        users: list[tuple[str, object]],
    ) -> None:
        """Insert a default ``NoteCreated`` rule per user."""
        for user_id, username in users:
            shelf_slug = users_shelf_slug_for(username)
            # Look up the user's shelf, then check whether a
            # rule already exists.  Both lookups are idempotent.
            shelf_row = await ctx.db.fetchrow(
                "SELECT id FROM note.shelf WHERE slug = $1",
                shelf_slug,
            )
            if not shelf_row or shelf_row.get("id") is None:
                continue
            shelf_id = str(shelf_row.get("id"))
            existing = await ctx.db.fetchrow(
                """
                SELECT id FROM rules
                WHERE event_type = 'NoteCreated'
                  AND attached_entity_type = 'shelf'
                  AND attached_entity_id = $1
                """,
                shelf_id,
            )
            if existing and existing.get("id") is not None:
                continue
            # Pick the first fleeting_notes row.  Single-tenant
            # deployments have exactly one such row.
            fleeting = await ctx.db.fetchrow(
                "SELECT id FROM note.directory WHERE slug = 'fleeting_notes' LIMIT 1"
            )
            if not fleeting or fleeting.get("id") is None:
                continue
            await ctx.db.execute(
                """
                INSERT INTO rules (
                    event_type, attached_entity_type, attached_entity_id,
                    condition, action_type, action_context,
                    enabled, creator_id
                ) VALUES (
                    'NoteCreated', 'shelf', $1,
                    $2::jsonb, 'add_to_directory', $3::jsonb,
                    TRUE, $4
                )
                """,
                shelf_id,
                json.dumps({"type": "always_true"}),
                json.dumps({"directory_id": str(fleeting.get("id"))}),
                user_id,
            )


__all__ = ["Migration"]