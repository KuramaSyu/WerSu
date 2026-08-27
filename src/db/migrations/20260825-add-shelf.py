"""Add shelves, widen ``directory.parent`` and remove global rules.

This single migration ships three coordinated changes that have to
land together:

* **SpiceDB schema** -- introduce the ``shelf`` definition and
  widen ``directory.parent`` from ``directory`` to
  ``directory | shelf`` so a book (directory) may be attached to
  one or more shelves.  The full schema is embedded in this file
  rather than read from ``schema.zed`` so the migration stays
  self-contained, matching the pattern used by every other
  post-initial schema migration.
* **Postgres tables** -- create ``note.shelf`` (with the same
  metadata columns as ``note.directory`` including
  ``readme_note_id`` so shelves can carry a README pointer
  exactly like books do) and the m2m ``note.shelf_book`` table
  for the shelf<->book relationship.  Shelves are flat by
  design -- no nesting.
* **Drop global rules** -- rules that previously had
  ``attached_entity_type`` or ``attached_entity_id`` unset are
  no longer valid under the new model, so this migration deletes
  them.  No data is rewritten; the rows simply go away.

The full new schema text is below as a literal.  Keep this in
sync with ``schema.zed`` (and any future migration that mutates
the schema).
"""

from __future__ import annotations

from authzed.api.v1 import WriteSchemaRequest

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


# Full SpiceDB schema as it should exist after this migration.
# ``directory.parent`` is widened to ``directory | shelf`` and a
# new ``definition shelf`` is added.  All other definitions are
# preserved unchanged from the previous migration history.
SHELF_SCHEMA_ZED = """\
definition user {}

definition role {
    relation administrator: user | role#member
    relation member: user

    permission manage = administrator
}

definition shelf {
    relation owner: user | role#member
    relation admin: user | role#member
    relation writer: user | role#member
    relation reader: user | role#member

    permission delete = admin
    permission write = writer + admin
    permission view = reader + write
    permission edit_permissions = admin + owner
}

definition directory {
    relation parent: directory | shelf
    relation owner: user | role#member
    relation admin: user | role#member
    relation writer: user | role#member
    relation reader: user | role#member

    permission delete = admin
    permission write = writer + admin
    permission view = reader + write
    permission edit_permissions = admin + owner
}

definition note {
    relation owner: user | role#member
    relation admin: user | role#member
    relation writer: user | role#member
    relation reader: user | role#member
    relation parent_directory: directory

    permission delete = owner + admin + parent_directory->delete
    permission write = owner + writer + admin + parent_directory->write
    permission view = reader + write
    permission edit_permissions = owner + admin + parent_directory->edit_permissions
    permission manage = owner + admin + parent_directory->admin
}

definition attachment {
    relation parent_note: note
    relation parent_user: user

    permission delete = parent_note->delete + parent_user
    permission write = parent_note->write + parent_user
    permission view = parent_note->view + parent_user
}
"""


class Migration(MigrationABC):
    """Add the shelf tables + widen SpiceDB directory.parent + drop global rules."""

    async def up(self, ctx: MigrationContext) -> None:
        """Apply the schema changes in a single ``up()``.

        Order:

        1. Postgres tables (``note.shelf`` + ``note.shelf_book`` +
           indexes / uniques).
        2. SpiceDB schema write -- must succeed before any code
           reads ``directory.parent`` against a shelf subject.
        3. Delete pre-existing global rules.  Done last so a
           crash before this point leaves the data intact.
        """
        # 1. Postgres: shelf + shelf_book m2m.
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS note.shelf (
                id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                slug              text NOT NULL,
                display_name      text,
                description       text,
                image_url         text,
                readme_note_id    text,
                created_at        timestamptz NOT NULL DEFAULT now(),
                updated_at        timestamptz NOT NULL DEFAULT now()
            );

            -- Ownership lives in SpiceDB (``shelf#owner@user``);
            CREATE UNIQUE INDEX IF NOT EXISTS shelf_slug_unique
                ON note.shelf (slug);

            CREATE TABLE IF NOT EXISTS note.shelf_book (
                id        BIGSERIAL PRIMARY KEY,
                shelf_id  uuid NOT NULL
                    REFERENCES note.shelf(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE,
                book_id   TEXT NOT NULL
                    REFERENCES note.directory(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE
            );

            CREATE UNIQUE INDEX IF NOT EXISTS shelf_book_unique
                ON note.shelf_book (shelf_id, book_id);

            CREATE INDEX IF NOT EXISTS shelf_book_book_idx
                ON note.shelf_book (book_id);
            """
        )

        # 2. SpiceDB schema write.
        if ctx.spicedb_client is None:
            raise ValueError(
                "MigrationContext.spicedb_client is required for "
                "SpiceDB schema migration"
            )
        await ctx.spicedb_client.WriteSchema(
            WriteSchemaRequest(schema=SHELF_SCHEMA_ZED)
        )

        # 3. Drop global rules.  A row is "global" iff either of
        # the attached_entity_* columns is NULL; under the new
        # model both must be set.
        await ctx.db.execute(
            """
            DELETE FROM rules
            WHERE attached_entity_type IS NULL
               OR attached_entity_id IS NULL
            """
        )