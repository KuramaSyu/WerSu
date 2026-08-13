"""Add the role-based access control (RBAC) subsystem.

This single migration sets up both halves of the role system:

* a Postgres ``roles`` table that holds role metadata (id, name,
  description, created_at);
* a SpiceDB schema update that introduces the ``role`` definition,
  the ``user#member_of@role`` edge, ``role#administrator`` /
  ``role#manage``, and ``note#manage`` (cascades from
  ``directory#admin`` via ``parent_directory->admin``).

The SpiceDB schema text is embedded as a string literal rather than
read from ``schema.zed`` so that this migration is self-contained:
later schema changes must add their own migrations with their own
copy of the schema text they want SpiceDB to converge to.  Reading
``schema.zed`` at migration time would silently ship the *current*
schema and mask drift between the migration history and the working
schema file.
"""

from authzed.api.v1 import WriteSchemaRequest

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


# The full authorization schema as it should exist after this
# migration.  Embed it as a triple-quoted string so the migration
# stays self-contained: a future change to ``schema.zed`` does NOT
# change what this migration writes, which is the desired behaviour
# for an immutable migration history.
_ROLE_SCHEMA_ZED = """\
definition user {}

definition role {
    relation administrator: user | role#member
    relation member: user

    permission manage = administrator
}

definition directory {
    relation parent: directory
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

    /**
    * permission delete is granted to admins or parent
    * directory users with delete permission
    */
    permission delete = owner + admin + parent_directory->delete
    permission write = owner + writer + admin + parent_directory->write
    permission view = reader + write
    permission edit_permissions = owner + admin + parent_directory->edit_permissions
    permission manage = admin + parent_directory->admin
}

definition attachment {
    relation parent_note: note
    relation parent_user: user

    // each user who can view any note (of this attachment), can also view this attachment.
    // there are no separate relations here, that a user can see some of the attachments of a note.
    // parent_user grants the owner of an attachment full CRUD on it, even when it has no
    // parent note (orphaned attachments).
    permission delete = parent_note->delete + parent_user
    permission write = parent_note->write + parent_user
    permission view = parent_note->view + parent_user
}
"""


class Migration(MigrationABC):
    """Create the ``roles`` Postgres table and the matching SpiceDB schema."""

    async def up(self, ctx: MigrationContext) -> None:
        """Apply both halves of the migration in one shot.

        Order matters: the SpiceDB schema must land before any
        service tries to read ``role`` objects, and Postgres must
        hold the metadata table before any role is created.  Doing
        both in a single ``up()`` keeps the two stores consistent
        at the migration boundary.
        """
        if ctx.spicedb_client is None:
            raise ValueError(
                "MigrationContext.spicedb_client is required for SpiceDB schema migration"
            )

        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS roles (
                id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                name TEXT NOT NULL UNIQUE,
                description TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # ``get_roles(name=...)`` is the most common lookup pattern;
        # the UNIQUE constraint already gives us a backing index, so
        # no additional index is needed here.

        await ctx.spicedb_client.WriteSchema(
            WriteSchemaRequest(schema=_ROLE_SCHEMA_ZED)
        )