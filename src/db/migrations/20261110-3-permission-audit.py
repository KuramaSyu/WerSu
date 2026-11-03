"""Create the dedicated ``permission_audit`` trail.

One row per successful direct grant/revoke.  Kept separate from the
user-facing ``activity`` log so security-audit rows do not leak into
activity statistics and so shelves/roles share one generic tuple shape.
"""

from __future__ import annotations

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Create ``public.permission_audit`` and its query indexes."""

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS public.permission_audit (
                id               TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                actor_id         TEXT NULL REFERENCES auth.user(id)
                                     ON DELETE SET NULL ON UPDATE CASCADE,
                accessed_as      TEXT NOT NULL DEFAULT 'user'
                                     CHECK (accessed_as IN ('user', 'system')),
                action           TEXT NOT NULL CHECK (action IN ('grant', 'revoke')),
                resource_type    TEXT NOT NULL,
                resource_id      TEXT NOT NULL,
                relation         TEXT NOT NULL,
                subject_type     TEXT NOT NULL,
                subject_id       TEXT NOT NULL,
                subject_relation TEXT NULL,
                metadata         JSONB NOT NULL DEFAULT '{}'::jsonb
            );

            CREATE INDEX IF NOT EXISTS idx_permission_audit_resource_at
                ON public.permission_audit (resource_type, resource_id, at DESC);
            CREATE INDEX IF NOT EXISTS idx_permission_audit_actor_at
                ON public.permission_audit (actor_id, at DESC)
                WHERE actor_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_permission_audit_at_brin
                ON public.permission_audit USING BRIN (at);
            """
        )