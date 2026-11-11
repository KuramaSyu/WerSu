"""Let a directory's ``delete`` follow its ``parent``.

A book (directory) is grouped by one or more shelves.  A caller who
may delete a shelf should be able to delete the shelf's books too, and
the recursive directory cascade must stay authorised by the same
grant.  Adding ``parent->delete`` to ``directory#delete`` makes that
transitive: ``parent`` is a directory or a shelf, both of which define
``delete``.

Callers that delete a shelf must delete its books *first* -- once the
shelf row is gone its ``parent`` edge and the grant it provides are
gone too.

The full updated schema is embedded as a literal to match the pattern
used by every other post-initial schema migration.  Keep it in sync
with ``schema.zed``.
"""

from __future__ import annotations

from authzed.api.v1 import WriteSchemaRequest

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


DIRECTORY_DELETE_FOLLOWS_PARENT_SCHEMA_ZED = """definition user {}

definition role {
    relation admin: user | role#member
    relation member: user

    // Delegable membership capabilities.
    relation grant_member: user | role#member
    relation delegate_member: user | role#member

    // Synthetic permission: callers that need "admin or owner"
    // semantics can use the same name across every resource type.
    permission has_admin = admin

    // May write/remove #member
    permission can_grant_member = admin + grant_member + delegate_member
    // May write/remove #grant_member
    permission can_delegate_member = admin + delegate_member
    // May write/remove #delegate_member
    permission can_assign_delegate_member = admin
}

definition page {
    relation admin: user | role#member
    relation grant_page_admin: user | role#member
    relation delegate_page_admin: user | role#member

    permission has_admin = admin

    // May write/remove #admin
    permission can_grant_page_admin = admin + grant_page_admin + delegate_page_admin
    // May write/remove #grant_page_admin
    permission can_delegate_page_admin = admin + delegate_page_admin
    // May write/remove #delegate_page_admin
    permission can_assign_delegate_page_admin = admin
}

definition shelf {
    relation owner: user | role#member
    relation admin: user | role#member
    relation writer: user | role#member
    relation reader: user | role#member

    relation grant_read: user | role#member
    relation grant_write: user | role#member
    relation grant_admin: user | role#member
    relation delegate_read: user | role#member
    relation delegate_write: user | role#member
    relation delegate_admin: user | role#member

    // May write/remove access relations
    permission can_grant_read = admin + owner + grant_read + delegate_read
    permission can_grant_write = admin + owner + grant_write + delegate_write
    permission can_grant_admin = owner + grant_admin + delegate_admin

    // May write/remove grant_* relations
    permission can_delegate_read = admin + owner + delegate_read
    permission can_delegate_write = admin + owner + delegate_write
    permission can_delegate_admin = owner + delegate_admin

    // May write/remove delegate_* relations
    permission can_assign_delegate_read = admin + owner
    permission can_assign_delegate_write = admin + owner
    permission can_assign_delegate_admin = owner

    permission has_admin = admin + owner
    permission delete = has_admin
    permission write = writer + has_admin
    permission view = reader + write
}

definition directory {
    relation parent: directory | shelf
    relation owner: user | role#member
    relation admin: user | role#member
    relation writer: user | role#member
    relation reader: user | role#member

    relation grant_read: user | role#member
    relation grant_write: user | role#member
    relation grant_admin: user | role#member
    relation delegate_read: user | role#member
    relation delegate_write: user | role#member
    relation delegate_admin: user | role#member

    permission can_grant_read = admin + owner + grant_read + delegate_read
    permission can_grant_write = admin + owner + grant_write + delegate_write
    permission can_grant_admin = owner + grant_admin + delegate_admin
    permission can_delegate_read = admin + owner + delegate_read
    permission can_delegate_write = admin + owner + delegate_write
    permission can_delegate_admin = owner + delegate_admin
    permission can_assign_delegate_read = admin + owner
    permission can_assign_delegate_write = admin + owner
    permission can_assign_delegate_admin = owner

    // owner is a full admin-equivalent so an owner-only directory can
    // view/write/delete just like an admin.
    permission has_admin = admin + owner
    // ``parent`` is a directory or a shelf.  Following it here lets a
    // caller who may delete a shelf delete its books too, and lets the
    // recursive directory cascade run under the same permission.  The
    // caller must delete the books *before* the shelf: once the shelf
    // row is gone the ``parent`` edge (and this grant) is gone with it.
    permission delete = has_admin + parent->delete
    permission write = writer + has_admin
    permission view = reader + write
}

definition note {
    relation parent_directory: directory
    relation owner: user | role#member
    relation admin: user | role#member
    relation writer: user | role#member
    relation reader: user | role#member

    relation grant_read: user | role#member
    relation grant_write: user | role#member
    relation grant_admin: user | role#member
    relation delegate_read: user | role#member
    relation delegate_write: user | role#member
    relation delegate_admin: user | role#member

    permission can_grant_read = admin + owner + grant_read + delegate_read
    permission can_grant_write = admin + owner + grant_write + delegate_write
    permission can_grant_admin = owner + grant_admin + delegate_admin
    permission can_delegate_read = admin + owner + delegate_read
    permission can_delegate_write = admin + owner + delegate_write
    permission can_delegate_admin = owner + delegate_admin
    permission can_assign_delegate_read = admin + owner
    permission can_assign_delegate_write = admin + owner
    permission can_assign_delegate_admin = owner

    // has_admin cascades from the parent directory so note-level
    // settings/rules stay manageable by directory admins.
    permission has_admin = owner + admin + parent_directory->has_admin
    permission delete = has_admin + parent_directory->delete
    permission write = owner + writer + admin + parent_directory->write
    permission view = reader + write
}

definition attachment {
    relation parent_note: note
    relation parent_user: user

    permission delete = parent_note->delete + parent_user
    permission write = parent_note->write + parent_user
    permission view = parent_note->view + parent_user
}"""


class Migration(MigrationABC):
    """Add ``parent->delete`` to the ``directory`` delete permission."""

    async def up(self, ctx: MigrationContext) -> None:
        if ctx.spicedb_client is None:
            raise ValueError(
                "MigrationContext.spicedb_client is required for "
                "the directory delete schema migration"
            )
        await ctx.spicedb_client.WriteSchema(
            WriteSchemaRequest(
                schema=DIRECTORY_DELETE_FOLLOWS_PARENT_SCHEMA_ZED
            )
        )
