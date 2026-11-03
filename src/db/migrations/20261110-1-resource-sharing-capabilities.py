"""Add the resource-sharing grant/delegate capabilities.

Embeds the full schema from ``schema.zed`` so the migration stays
self-contained (the runner does not read files from disk at runtime).
The schema removes the legacy ``edit_permissions`` / ``manage``
permissions and adds the bounded grant/delegate relations.
"""

from __future__ import annotations

from authzed.api.v1 import WriteSchemaRequest

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


RESOURCE_SHARING_SCHEMA = '''definition user {}

definition role {
    relation administrator: user | role#member
    relation member: user

    // Delegable membership capabilities.
    relation grant_member: user | role#member
    relation delegate_member: user | role#member

    // May write/remove #member
    permission can_grant_member = administrator + grant_member + delegate_member
    // May write/remove #grant_member
    permission can_delegate_member = administrator + delegate_member
    // May write/remove #delegate_member
    permission can_assign_delegate_member = administrator
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
    permission delete = has_admin
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
}
'''


class Migration(MigrationABC):
    """Write the resource-sharing schema."""

    async def up(self, ctx: MigrationContext) -> None:
        if ctx.spicedb_client is None:
            raise ValueError(
                "MigrationContext.spicedb_client is required for "
                "SpiceDB schema migration"
            )
        await ctx.spicedb_client.WriteSchema(
            WriteSchemaRequest(schema=RESOURCE_SHARING_SCHEMA)
        )
