"""Add ``note#manage = owner + admin + parent_directory->admin`` to SpiceDB.

The rules subsystem gates rule creation on ``manage`` permission
on the attached entity.  ``directory#edit_permissions`` already
covers ``owner + admin`` (so owners can manage their directory's
rules), but ``note#manage`` was previously defined as
``admin + parent_directory->admin`` -- excluding note owners.

This migration extends ``note#manage`` to include ``owner`` so
note owners can create rules on notes they own without being
admins.  The semantic is "if you can manage the permissions on
the note, you can manage rules attached to it too".

Both expressions are combined via ``+`` (SpiceDB union), keeping
the existing inheritance from the parent directory intact.

The full ``schema.zed`` text is inlined in this migration (not
read from disk at runtime) so the migration is self-contained
and a future schema change that affects an unrelated resource
cannot silently drop this update.
"""

from __future__ import annotations

from authzed.api.v1 import WriteSchemaRequest

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


# The full SpiceDB schema, inlined so this migration is
# self-contained.  Keep in sync with ``schema.zed``; the only
# line that differs from a vanilla ``schema.zed`` write is the
# ``note#manage`` expression, which adds ``owner`` to the
# original ``admin + parent_directory->admin`` union.
INLINE_SCHEMA = """\
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
    permission manage = owner + admin + parent_directory->admin
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
    """Write the inlined ``note#manage = owner + admin + ...`` schema."""

    async def up(self, ctx: MigrationContext) -> None:
        """Write the inlined schema into SpiceDB.

        Raises:
            ValueError: when the migration context was constructed
                without a SpiceDB client.
        """
        if ctx.spicedb_client is None:
            raise ValueError(
                "MigrationContext.spicedb_client is required for SpiceDB "
                "schema migration"
            )
        await ctx.spicedb_client.WriteSchema(
            WriteSchemaRequest(schema=INLINE_SCHEMA),
        )
