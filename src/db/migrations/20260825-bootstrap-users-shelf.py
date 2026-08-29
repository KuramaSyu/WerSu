"""Backfill users_shelf for users that predate the shelf tables.

Idempotent on every step.  This migration owns the only thing
the live ``ZettelkastenStrategy`` does not: the ``note.shelf``
row itself (the strategy's contract says "not insert the shelf
itself -- the caller already did").

The strategy owns everything else (default books,
``shelf_book`` bindings, the default ``NoteCreated`` rule) and
is invoked through the ``zettelkasten_strategy`` service the
migration runner injects from the live composition root.  The
shelf repo's ``writes_user_permissions`` decorator grants the
``shelf#owner`` / ``shelf#admin`` edges when the strategy
binds the user as the shelf owner, so this migration no longer
needs to write any SpiceDB relations directly.

This keeps the backfill in lockstep with
``user_service.create_user`` instead of reimplementing shelf /
book / rule creation in migration land.
"""

from __future__ import annotations

from typing import Optional

from src.api.other.user_context import UserContextABC
from src.db.entities.shelf import ShelfEntity
from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext
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
        for user_id, username in users:
            user_ctx = await self._build_user_ctx(ctx, user_id)
            if user_ctx is None:
                continue
            shelf_entity = await self._ensure_shelf_row(
                ctx, username, user_ctx,
            )
            if shelf_entity is None:
                continue
            await self._run_zettelkasten_bootstrap(
                ctx, shelf_entity, user_id, user_ctx,
            )

    async def _build_user_ctx(
        self,
        ctx: MigrationContext,
        user_id: str,
    ) -> Optional[UserContextABC]:
        """Materialise a ``UserContextABC`` for ``user_id`` if the factory is wired."""
        factory = ctx.services.user_context_factory
        if factory is None:
            return None
        return await factory.create(str(user_id))

    async def _ensure_shelf_row(
        self,
        ctx: MigrationContext,
        username: object,
        user_ctx: UserContextABC,
    ) -> Optional[ShelfEntity]:
        """Insert the user's ``note.shelf`` row on first run; reuse on re-runs.

        On a fresh insert, delegates to the live
        :meth:`ShelfRepoABC.insert_shelf` so the
        :func:`~src.db.repos.shelf.postgres.writes_user_permissions`
        decorator grants ``shelf#owner`` and ``shelf#admin``
        for ``user_ctx`` in the same call.

        Returns the shelf entity (id guaranteed set) so the
        caller can hand it to the strategy.
        """
        shelf_repo = ctx.services.shelf_repo
        if shelf_repo is None:
            # Fixture-only / Postgres-only run: fall back to
            # the raw SQL probe + insert.  No SpiceDB grants
            # happen in this mode by design.
            return await self._ensure_shelf_row_raw(ctx, username)

        existing = await ctx.db.fetchrow(
            "SELECT id FROM note.shelf WHERE slug = $1",
            users_shelf_slug_for(username),
        )
        if existing and existing.get("id") is not None:
            return ShelfEntity(
                id=str(existing.get("id")),
                slug=users_shelf_slug_for(username),
            )

        persisted = await shelf_repo.insert_shelf(
            slug=users_shelf_slug_for(username),
            display_name=users_shelf_display_name_for(username),
            description=USERS_SHELF_DESCRIPTION,
            user_ctx=user_ctx,
        )
        return persisted

    async def _ensure_shelf_row_raw(
        self,
        ctx: MigrationContext,
        username: object,
    ) -> Optional[ShelfEntity]:
        """Raw-SQL fallback when no shelf repo is registered.

        Mirrors :meth:`_ensure_shelf_row` but skips the
        SpiceDB grant -- the Postgres-only fixture runs that
        hit this branch don't exercise the default-fleeting
        code path.
        """
        shelf_slug = users_shelf_slug_for(username)
        existing = await ctx.db.fetchrow(
            "SELECT id, slug FROM note.shelf WHERE slug = $1",
            shelf_slug,
        )
        if existing and existing.get("id") is not None:
            return ShelfEntity(
                id=str(existing.get("id")),
                slug=shelf_slug,
            )

        inserted = await ctx.db.fetchrow(
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
        if not inserted or inserted.get("id") is None:
            return None
        return ShelfEntity(
            id=str(inserted.get("id")),
            slug=shelf_slug,
        )

    async def _run_zettelkasten_bootstrap(
        self,
        ctx: MigrationContext,
        shelf: ShelfEntity,
        user_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        """Delegate default books / bindings / rule to ``ZettelkastenStrategy``.

        The strategy calls ``shelf_repo.add_book(...)`` /
        ``set_books_of(...)`` with the ``user_ctx`` we built in
        :meth:`up`, which lets the
        :func:`~src.db.repos.shelf.postgres.writes_user_permissions`
        decorator grant ``directory#owner`` + ``directory#admin``
        on the newly added books without this migration needing
        to touch SpiceDB directly.

        Skipped when the strategy is not registered -- that
        happens on fixture-only / CLI runs that run migrations
        without a full service bundle.  Those runs do not
        exercise the default-fleeting code path, so the shelf
        without books is harmless.
        """
        strategy = ctx.services.zettelkasten_strategy
        if strategy is None:
            return
        await strategy.apply(
            shelf=shelf,
            owner_id=str(user_id),
            user_ctx=user_ctx,
        )


__all__ = ["Migration"]
