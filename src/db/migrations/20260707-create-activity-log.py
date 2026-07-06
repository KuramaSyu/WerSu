"""Create the cross-cutting ``activity`` log.

Tracks user actions against notes, directories, and role assignments.
Lives in the default ``public`` schema because it spans every domain
object (notes, directories, users).  Mirrors the layout of
``20260620-create-share-relation`` (one ENUM per concern, table
created in the same migration that introduces the ENUMs).

Schema shape:

* ``activity_kind``  -- ENUM of every recognisable action.  Past-tense
  naming convention so the kind reads as the event that *happened*
  (``note_viewed``, ``role_grant``, ...).
* ``activity``       -- append-only log row.  The target shape is
  implicit in ``action``: ``note_*`` events set ``note_id``;
  ``directory_*`` events set ``directory_id``; ``role_*`` events set
  ``role_id`` and no note / directory column.  The application layer
  enforces that shape (see :class:`ActivityLoggerService`); the schema
  deliberately has no CHECK so each row can evolve without a DDL
  round-trip.

The ``accessed_as`` column records whether the actor was the user
themselves or the system acting on their behalf (``"user"`` /
``"system"``).  Python attributes the same field as ``accessed_as``
because ``as`` is a reserved word.
"""

from __future__ import annotations

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Create the ``activity`` table, its kind ENUM and supporting indexes."""

    async def up(self, ctx: MigrationContext) -> None:
        """Apply the activity-log schema in a single execute call.

        Notes
        -----
        - All statements run inside the implicit per-call transaction
          provided by :class:`src.db.database.acquire`, matching the
          convention used by every other migration.
        """
        await ctx.db.execute(
            """
            CREATE TYPE activity_kind AS ENUM (
                'note_viewed',
                'note_created',
                'note_edited',
                'note_deleted',
                'note_published',
                'note_shared',
                'note_restored',
                'note_archived',
                'note_version_restored',
                'note_attachment_added',
                'directory_created',
                'directory_viewed',
                'directory_edited',
                'directory_deleted',
                'role_grant',
                'role_revoke',
                'role_change'
            );

            CREATE TABLE IF NOT EXISTS activity (
                id            TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                actor_id      TEXT NULL REFERENCES users(id) ON DELETE SET NULL ON UPDATE CASCADE,
                accessed_as   TEXT NOT NULL DEFAULT 'user'
                                  CHECK (accessed_as IN ('user', 'system')),
                action        activity_kind NOT NULL,
                note_id       TEXT NULL,
                directory_id  TEXT NULL,
                role_id       TEXT NULL,
                at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
            );

            CREATE INDEX IF NOT EXISTS idx_activity_note_at
                ON activity (note_id, at DESC) WHERE note_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_activity_directory_at
                ON activity (directory_id, at DESC) WHERE directory_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_activity_view_at
                ON activity (note_id, at DESC) WHERE action = 'note_viewed';
            CREATE INDEX IF NOT EXISTS idx_activity_actor_at
                ON activity (actor_id, at DESC) WHERE actor_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_activity_accessed_as_at
                ON activity (accessed_as, at DESC);
            CREATE INDEX IF NOT EXISTS idx_activity_role_id_at
                ON activity (role_id, at DESC) WHERE role_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_activity_at_brin
                ON activity USING BRIN (at);
            """
        )