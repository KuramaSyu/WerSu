"""Move the ``users`` table into the new ``auth`` schema and split credentials out.

The auth surface grew past the single ``users`` table -- Discord
ids, password hashes and passkey records each have their own
identity and can be linked to multiple users.  This migration:

1. Creates the ``auth`` schema.
2. Renames ``public.users`` to ``auth.user``.
3. Drops ``discord_id`` from ``auth.user`` (moved to
   ``auth.third_party``); keeps the remaining identity columns.
4. Adds ``auth.password`` (one row per user -- password hash) and
   ``auth.passkey`` (many rows per user -- WebAuthn credentials).
5. Adds ``auth.third_party`` (one row per linked provider --
   discord, google).  ``(provider, provider_user_id)`` is unique.
6. Re-points every foreign key that previously targeted
   ``public.users`` to ``auth.user``.

Postgres does not allow renaming a table that is the target of a
foreign key without dropping the FK first; we drop and re-add
each FK around the rename so the move is atomic from the caller's
perspective.

Structural statements are idempotent (``CREATE TABLE IF NOT
EXISTS``); the rename only fires when the old ``users`` table
exists and the new ``auth.user`` table does not.
"""

from __future__ import annotations

from src.db.entities.user.user import UserEntity
from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


# (table, constraint_name, column, on_delete)
# since we kill all FKs and constraints which currently point to the users table,
# we list them here, to update FKs and constraints to auth.user table.
#
# Postgres strips the schema prefix when generating default FK
# constraint names for tables in non-``public`` schemas.  The names
# below match what Postgres produced at table-creation time.
_FKS_TO_REPOINT: list[tuple[str, str, str, str]] = [
    ("note.content", "content_author_id_fkey", "author_id", "CASCADE"),
    ("note.version_snapshot", "version_snapshot_author_id_fkey", "author_id", "CASCADE"),
    ("note.version_delta", "version_delta_author_id_fkey", "author_id", "CASCADE"),
    ("note.attachment", "attachment_created_by_fkey", "created_by", "SET NULL"),
    ("activity", "activity_actor_id_fkey", "actor_id", "SET NULL"),
    ("shared", "shared_created_by_fkey", "created_by", "CASCADE"),
    ("shared", "shared_access_as_fkey", "access_as", "CASCADE"),
    ("user_action", "user_action_user_id_fkey", "user_id", "CASCADE"),
]


class Migration(MigrationABC):
    """Split users into ``auth.user`` + credential tables."""

    async def up(self, ctx: MigrationContext) -> None:
        """Apply the auth schema in a single transactional execute."""
        await ctx.db.execute(
            """
            CREATE SCHEMA IF NOT EXISTS auth;

            -- auth.user is referenced by most other tables -> create this first
            CREATE TABLE IF NOT EXISTS auth.user (
                id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                avatar TEXT NULL,
                username TEXT NULL,
                email TEXT NULL,
                type user_kind NOT NULL DEFAULT 'human'
            );

            CREATE TABLE IF NOT EXISTS auth.password (
                user_id TEXT PRIMARY KEY REFERENCES auth.user(id)
                    ON DELETE CASCADE ON UPDATE CASCADE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS auth.passkey (
                id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                user_id TEXT NOT NULL REFERENCES auth.user(id)
                    ON DELETE CASCADE ON UPDATE CASCADE,
                credential_id BYTEA NOT NULL,
                public_key BYTEA NOT NULL,
                sign_count BIGINT NOT NULL DEFAULT 0,
                transports TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                aaguid BYTEA NULL,
                backup_eligible BOOLEAN NOT NULL DEFAULT FALSE,
                backup_state BOOLEAN NOT NULL DEFAULT FALSE,
                user_verified BOOLEAN NOT NULL DEFAULT FALSE,
                friendly_name TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                last_used_at TIMESTAMP NULL,
                revoked_at TIMESTAMP NULL
            );

            CREATE INDEX IF NOT EXISTS auth_passkey_user_idx
                ON auth.passkey (user_id);

            CREATE TABLE IF NOT EXISTS auth.third_party (
                id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                user_id TEXT NOT NULL REFERENCES auth.user(id)
                    ON DELETE CASCADE ON UPDATE CASCADE,
                provider TEXT NOT NULL CHECK (provider IN ('discord', 'google')),
                provider_user_id TEXT NOT NULL,
                extra_fields JSONB NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (provider, provider_user_id)
            );

            CREATE INDEX IF NOT EXISTS auth_third_party_user_idx
                ON auth.third_party (user_id);
            """
        )

        # Move the existing ``users`` table to ``auth.user`` once.
        # ``auth.user`` is already created (empty) above, so the only
        # condition we gate on is whether ``public.users`` still has
        # rows to copy.  The move is idempotent thanks to
        # ``ON CONFLICT (id) DO NOTHING`` in the SQL below.
        users_exists = await ctx.db.fetch(
            "SELECT to_regclass('public.users') AS regclass"
        )
        if users_exists[0]["regclass"] is not None:
            await self._move_users_table(ctx)

        # Seed the public system user.  Mirrors the seed in
        # ``20260620-create-share-relation``; the share code now
        # points at ``auth.user`` so the seed needs re-applying
        # against the new location.
        user = UserEntity(username="public_user", type="system")
        await ctx.db.execute(
            """
            INSERT INTO auth.user (username, type)
            VALUES ($1, $2)
            ON CONFLICT (id) DO NOTHING;
            """,
            user.username,
            user.type,
        )

    async def _move_users_table(self, ctx: MigrationContext) -> None:
        """Repoint every FK, copy ``public.users`` rows into ``auth.user``.

        ``auth.user`` is already created by :meth:`up` before this is
        called, so we copy rows in rather than re-creating the table.
        ``ON CONFLICT (id) DO NOTHING`` keeps the move idempotent in
        case the migration is partially re-run.
        """
        drop_fk_stmts = "\n            ".join(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint};"
            for table, constraint, _column, _on_delete in _FKS_TO_REPOINT
        )
        add_fk_stmts = "\n            ".join(
            f"ALTER TABLE {table} "
            f"ADD CONSTRAINT {constraint} "
            f"FOREIGN KEY ({column}) REFERENCES auth.user(id) "
            f"ON DELETE {on_delete} "
            f"ON UPDATE CASCADE;"
            for table, constraint, column, on_delete in _FKS_TO_REPOINT
        )

        await ctx.db.execute(
            f"""
            {drop_fk_stmts}

            INSERT INTO auth.user (id, avatar, username, email, type)
            SELECT id, avatar, username, email, type
            FROM public.users
            ON CONFLICT (id) DO NOTHING;

            -- back-fill third_party rows for the discord ids we
            -- just moved off the user row.  The legacy schema
            -- didn't have a discriminator column; the new JSON
            -- ``extra_fields`` carries it for OAuth signups.
            INSERT INTO auth.third_party (user_id, provider, provider_user_id, extra_fields)
            SELECT id, 'discord', discord_id::TEXT,
                   jsonb_build_object('discriminator', discriminator)
            FROM public.users
            WHERE discord_id IS NOT NULL
            ON CONFLICT (provider, provider_user_id) DO NOTHING;

            DROP TABLE public.users;

            {add_fk_stmts}
            """
        )
