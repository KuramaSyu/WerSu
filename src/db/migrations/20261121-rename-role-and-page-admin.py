"""Apply the role/page ``admin`` schema to databases where 20261120 already ran.

The original ``20261120-add-page-administrator`` migration was already
marked applied in some environments before ``role#administrator`` and
``page#page_admin`` were renamed to ``admin`` and before ``has_admin``
was added to both definitions.  Migration history is immutable, so this
follow-up migration:

1. writes a bridge schema that supports both old and new relation names;
2. copies legacy ``role#administrator`` / ``page#page_admin`` tuples to
   ``admin``;
3. deletes the legacy tuples;
4. writes the final schema with ``admin`` and ``has_admin``.

It is idempotent on fresh databases (where 20261120 already wrote the
final schema) and does nothing except re-write the same schema.
"""

from __future__ import annotations

from authzed.api.v1 import WriteSchemaRequest

from src.api import (
    ObjectRef,
    ObjectTypeEnum,
    PAGE_ROOT_ID,
    PageRelationEnum,
    PermissionRepoABC,
    Relationship,
    RoleRelationEnum,
    SubjectRef,
    UNDEFINED,
)
from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


FINAL_SCHEMA = """\
definition user {}

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
}"""


def _intermediate_schema() -> str:
    """Temporarily support both old and new relation names for the copy."""
    bridge = FINAL_SCHEMA.replace(
        "definition role {\n    relation admin: user | role#member",
        "definition role {\n"
        "    relation administrator: user | role#member\n"
        "    relation admin: user | role#member",
    )
    bridge = bridge.replace(
        "definition page {\n    relation admin: user | role#member",
        "definition page {\n"
        "    relation page_admin: user | role#member\n"
        "    relation admin: user | role#member",
    )
    return bridge


class Migration(MigrationABC):
    """Migrate legacy role/page admin tuples and apply the final schema."""

    async def up(self, ctx: MigrationContext) -> None:
        if ctx.spicedb_client is None:
            raise ValueError(
                "MigrationContext.spicedb_client is required for "
                "SpiceDB schema migration"
            )
        permission_repo = ctx.services.permission_repo
        if permission_repo is None:
            raise ValueError(
                "MigrationContext.services.permission_repo is required for "
                "role/page admin migration"
            )

        legacy = await self._read_legacy_relationships(permission_repo)
        if legacy:
            await ctx.spicedb_client.WriteSchema(
                WriteSchemaRequest(schema=_intermediate_schema())
            )
            for _old, new in legacy:
                await self._ensure_relationship(permission_repo, new)
            for old, _new in legacy:
                await permission_repo.delete(old)

        await ctx.spicedb_client.WriteSchema(
            WriteSchemaRequest(schema=FINAL_SCHEMA)
        )

    async def _read_legacy_relationships(
        self,
        permission_repo: PermissionRepoABC,
    ) -> list[tuple[Relationship, Relationship]]:
        legacy: list[tuple[Relationship, Relationship]] = []
        for resource_type, old_relation in (
            (ObjectTypeEnum.ROLE, "administrator"),
            (ObjectTypeEnum.PAGE, "page_admin"),
        ):
            for subject_type, optional_relation in (
                (ObjectTypeEnum.USER, None),
                (ObjectTypeEnum.ROLE, RoleRelationEnum.MEMBER),
            ):
                try:
                    found = await permission_repo.lookup_relationships(
                        Relationship(
                            resource=ObjectRef(resource_type, UNDEFINED),
                            relation=old_relation,
                            subject=SubjectRef(
                                subject_type,
                                UNDEFINED,
                                optional_relation=optional_relation,
                            ),
                        )
                    )
                except Exception:
                    # Legacy relation is absent: fresh install or retry.
                    continue
                for old in found:
                    legacy.append(
                        (
                            old,
                            Relationship(
                                resource=old.resource,
                                relation=PageRelationEnum.ADMIN,
                                subject=old.subject,
                            ),
                        )
                    )
        return legacy

    @staticmethod
    async def _ensure_relationship(
        permission_repo: PermissionRepoABC,
        relationship: Relationship,
    ) -> None:
        existing = await permission_repo.lookup_relationships(relationship)
        if existing:
            return
        await permission_repo.insert([relationship])


__all__ = ["Migration", "FINAL_SCHEMA"]
