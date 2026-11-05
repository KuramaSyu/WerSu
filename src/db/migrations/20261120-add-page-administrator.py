"""Seed the site-wide ``Page Administrator`` role.

Adds the ``page`` object to the SpiceDB schema.  Its ``admin``,
``grant_page_admin`` and ``delegate_page_admin`` relations are all
mappable to users and roles, with the usual grant/delegate capability
permissions.  It also renames the role relation ``admin`` to
``admin`` and adds ``has_admin`` to both ``role`` and ``page``.

The migration then:

1. creates (or reuses) the ``Page Administrator`` role row;
2. grants that role all three page-admin relations, so every member of
   the role is a page admin and may grant/delegate the
   permission in turn;
3. assigns the first real (``type = 'human'``) user -- ordered by the
   time-ordered UUID primary key -- to the role.  System and temporary
   users are deliberately ignored.

The embedded schema keeps the migration self-contained, matching the
existing schema migrations in this directory.
"""

from __future__ import annotations

from authzed.api.v1 import WriteSchemaRequest

from src.api import (
    PAGE_ROOT_ID,
    ObjectRef,
    ObjectTypeEnum,
    PageRelationEnum,
    PermissionRepoABC,
    Relationship,
    RoleRelationEnum,
    SubjectRef,
    UNDEFINED,
)
from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


PAGE_ADMINISTRATOR_ROLE_NAME = "Page Administrator"
PAGE_ADMINISTRATOR_ROLE_DESCRIPTION = (
    "Site-wide admin. Members may manage roles and delegate "
    "page-admin access."
)

PAGE_ADMIN_SCHEMA = """definition user {}

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
    """Return a schema that temporarily supports both old and new names.

    SpiceDB refuses to remove a relation while relationships still use it,
    so the migration first writes this bridge schema, copies the old tuples
    to the new ``admin`` relation, deletes the old tuples, and only then
    writes :data:`PAGE_ADMIN_SCHEMA`.
    """
    schema = PAGE_ADMIN_SCHEMA.replace(
        "definition role {\n    relation admin: user | role#member",
        "definition role {\n"
        "    relation administrator: user | role#member\n"
        "    relation admin: user | role#member",
    )
    schema = schema.replace(
        "definition page {\n    relation admin: user | role#member",
        "definition page {\n"
        "    relation page_admin: user | role#member\n"
        "    relation admin: user | role#member",
    )
    return schema


class Migration(MigrationABC):
    """Add the page schema, seed the Page Administrator role, assign the first user."""

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
                "page admin migration"
            )

        legacy = await self._read_legacy_relationships(permission_repo)
        if legacy:
            # SpiceDB will not drop a relation while tuples still reference
            # it.  Bridge old -> new first, then remove the old tuples.
            await ctx.spicedb_client.WriteSchema(
                WriteSchemaRequest(schema=_intermediate_schema())
            )
            for old, new in legacy:
                await self._ensure_relationship(permission_repo, new)
            for old, _new in legacy:
                await permission_repo.delete(old)

        await ctx.spicedb_client.WriteSchema(
            WriteSchemaRequest(schema=PAGE_ADMIN_SCHEMA)
        )

        role_id = await self._ensure_page_admin_role(ctx)
        await self._grant_page_admin_to_role(permission_repo, role_id)

        first_user_id = await self._first_real_user_id(ctx)
        if first_user_id is not None:
            await self._assign_role_to_user(permission_repo, role_id, first_user_id)

    async def _read_legacy_relationships(
        self,
        permission_repo: PermissionRepoABC,
    ) -> list[tuple[Relationship, Relationship]]:
        """Collect old relations that were renamed to ``admin``.

        The old names are ``role#administrator`` and ``page#page_admin``.
        Each entry is ``(old_tuple, new_tuple)``; the new tuple keeps the
        same resource and subject but uses the ``admin`` relation.
        """
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
                    # The old relation does not exist in the current schema
                    # (fresh install or a retry after the final schema write).
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

    async def _ensure_page_admin_role(self, ctx: MigrationContext) -> str:
        """Return the seeded role id, creating the row on first run."""
        row = await ctx.db.fetchrow(
            """
            INSERT INTO roles (name, description)
            VALUES ($1, $2)
            ON CONFLICT (name) DO NOTHING
            RETURNING id
            """,
            PAGE_ADMINISTRATOR_ROLE_NAME,
            PAGE_ADMINISTRATOR_ROLE_DESCRIPTION,
        )
        if row is not None and row.get("id") is not None:
            return str(row["id"])

        existing = await ctx.db.fetchrow(
            "SELECT id FROM roles WHERE name = $1",
            PAGE_ADMINISTRATOR_ROLE_NAME,
        )
        if existing is None or existing.get("id") is None:
            raise RuntimeError(
                f"Could not create or find role {PAGE_ADMINISTRATOR_ROLE_NAME!r}"
            )
        return str(existing["id"])

    async def _grant_page_admin_to_role(
        self,
        permission_repo: PermissionRepoABC,
        role_id: str,
    ) -> None:
        """Write the three page-admin relations for the seeded role.

        Keeping all three lets the role's members grant and delegate
        page-admin access as well as hold it directly.
        """
        for relation in (
            PageRelationEnum.ADMIN,
            PageRelationEnum.GRANT_PAGE_ADMIN,
            PageRelationEnum.DELEGATE_PAGE_ADMIN,
        ):
            await self._ensure_relationship(
                permission_repo,
                Relationship(
                    resource=ObjectRef(ObjectTypeEnum.PAGE, PAGE_ROOT_ID),
                    relation=relation,
                    # ``role:<id>#member`` grants this to every
                    # member of the Page Administrator role.
                    subject=SubjectRef(
                        ObjectTypeEnum.ROLE,
                        role_id,
                        optional_relation=RoleRelationEnum.MEMBER,
                    ),
                ),
            )

    async def _assign_role_to_user(
        self,
        permission_repo: PermissionRepoABC,
        role_id: str,
        user_id: str,
    ) -> None:
        await self._ensure_relationship(
            permission_repo,
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
                relation=RoleRelationEnum.MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            ),
        )

    @staticmethod
    async def _ensure_relationship(
        permission_repo: PermissionRepoABC,
        relationship: Relationship,
    ) -> None:
        """Insert ``relationship`` unless an identical tuple already exists."""
        existing = await permission_repo.lookup_relationships(relationship)
        if existing:
            return
        await permission_repo.insert([relationship])

    @staticmethod
    async def _first_real_user_id(ctx: MigrationContext) -> str | None:
        """Return the earliest-created human user, or ``None``.

        ``auth.user`` ids are UUIDv7, so ordering by ``id`` is ordering
        by creation time.  ``type = 'human'`` excludes the seeded
        ``public_user`` system account and temporary share users.
        """
        row = await ctx.db.fetchrow(
            """
            SELECT id
            FROM auth.user
            WHERE type = 'human'
            ORDER BY id ASC
            LIMIT 1
            """
        )
        if row is None or row.get("id") is None:
            return None
        return str(row["id"])


__all__ = [
    "Migration",
    "PAGE_ADMINISTRATOR_ROLE_NAME",
    "PAGE_ADMIN_SCHEMA",
]
