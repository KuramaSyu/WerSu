"""Derive ``shelf#admin`` from ``shelf#owner`` in the SpiceDB schema.

This migration is the schema counterpart to
:class:`src.db.repos.shelf.spicedb_decorator.SpicedbShelfRepoDecorator`
granting only ``shelf#owner`` on ``insert_shelf``: with this
schema change, an owner implicitly holds admin, so the
decorator no longer needs to write a separate ``shelf#admin``
edge.

Existing ``shelf#admin`` rows remain valid (the new
``admin: user | role#member | owner`` relation is a superset
of the previous one), so no data migration is required.

The full updated schema is embedded as a literal to match the
pattern used by every other post-initial schema migration
(e.g. ``20260825-add-shelf``).  Keep this in sync with
``schema.zed``.
"""

from __future__ import annotations

from authzed.api.v1 import WriteSchemaRequest

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


SHELF_ADMIN_FROM_OWNER_SCHEMA_ZED = """\
definition user {}

definition role {
    relation administrator: user | role#member
    relation member: user

    permission manage = administrator
}

definition shelf {
    relation owner: user | role#member
    relation admin: user | role#member | owner
    relation writer: user | role#member
    relation reader: user | role#member

    permission delete = admin
    permission write = writer + admin
    permission view = reader + write
    permission edit_permissions = admin + owner
}

definition directory {
    // A book (directory) can be nested inside other books or attached
    // to one or more shelves.  Multiple parents are supported: a book
    // may have several book-parents (DAG) and several shelf-parents.
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
    """Write the updated shelf schema so admin derives from owner."""

    async def up(self, ctx: MigrationContext) -> None:
        if ctx.spicedb_client is None:
            raise ValueError(
                "MigrationContext.spicedb_client is required for "
                "SpiceDB schema migration"
            )
        await ctx.spicedb_client.WriteSchema(
            WriteSchemaRequest(schema=SHELF_ADMIN_FROM_OWNER_SCHEMA_ZED)
        )
