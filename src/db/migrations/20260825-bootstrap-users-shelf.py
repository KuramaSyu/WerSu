"""Backfill users_shelf for users that predate the shelf tables.

Idempotent on every step: shelf row, shelf_book bindings, rule.
Step 3 (rule insert) delegates to ensure_default_fleeting_rule
so the probe is shared with the live zettelkasten bootstrap.
Steps 1+2 stay migration-specific because the backfill
semantics (bind every existing directory to every user shelf)
differ from the strategy's "create three fresh books".
"""

from __future__ import annotations

from typing import Optional

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext
from src.services.shelf_bootstrap.zettelkasten import (
    ensure_default_fleeting_rule,
)
from src.services.user_service import (
    USERS_SHELF_DESCRIPTION,
    users_shelf_display_name_for,
    users_shelf_slug_for,
)


class Migration(MigrationABC):

    async def up(self, ctx: MigrationContext) -> None:
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
        for user_id, username in users:
            shelf_slug = users_shelf_slug_for(username)
            existing = await ctx.db.fetchrow(
                "SELECT id FROM note.shelf WHERE slug = $1",
                shelf_slug,
            )
            if existing and existing.get("id") is not None:
                continue
            await ctx.db.fetchrow(
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

        # Single-tenant: bind every directory to every user shelf.
        # Multi-tenant backfill is out of scope here.
        shelves = await ctx.db.fetch("SELECT id, slug FROM note.shelf")
        dir_ids = await ctx.db.fetch("SELECT id FROM note.directory")
        for shelf in shelves:
            shelf_id = shelf.get("id")
            if shelf_id is None:
                continue
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
                    str(shelf_id),
                    str(did),
                )

    async def _attach_default_fleeting_rules(
        self,
        ctx: MigrationContext,
        users: list[tuple[str, object]],
    ) -> None:
        rule_repo = ctx.get("rule_repo")
        if rule_repo is None:
            await self._attach_default_fleeting_rules_raw(ctx, users)
            return

        for user_id, username in users:
            shelf_slug = users_shelf_slug_for(username)
            shelf_row = await ctx.db.fetchrow(
                "SELECT id FROM note.shelf WHERE slug = $1",
                shelf_slug,
            )
            if not shelf_row or shelf_row.get("id") is None:
                continue
            shelf_id = str(shelf_row.get("id"))

            fleeting = await ctx.db.fetchrow(
                "SELECT id FROM note.directory "
                "WHERE slug = 'fleeting_notes' LIMIT 1"
            )
            fleeting_id: Optional[str] = (
                str(fleeting.get("id"))
                if fleeting and fleeting.get("id") is not None
                else None
            )

            await ensure_default_fleeting_rule(
                rule_repo=rule_repo,
                shelf_id=shelf_id,
                owner_id=str(user_id),
                fleeting_directory_id=fleeting_id,
            )

    async def _attach_default_fleeting_rules_raw(
        self,
        ctx: MigrationContext,
        users: list[tuple[str, object]],
    ) -> None:
        # Raw-SQL fallback for fixtures/CLI runs without rule_repo.
        # Mirrors ensure_default_fleeting_rule: probe first, insert only if missing.
        for user_id, username in users:
            shelf_slug = users_shelf_slug_for(username)
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
            fleeting = await ctx.db.fetchrow(
                "SELECT id FROM note.directory "
                "WHERE slug = 'fleeting_notes' LIMIT 1"
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
                '{"type": "always_true"}',
                '{"directory_id": "' + str(fleeting.get("id")) + '"}',
                str(user_id),
            )


__all__ = ["Migration"]