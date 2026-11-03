"""Integration tests for :class:`NotePermissionRepoSpicedb`.

The ``spicedb_permission_repo`` fixture (from ``tests/fixtures/spicedb.py``)
spins up a real SpiceDB container once per test and applies the canonical
schema.  The two tests below exercise the production-style ``insert`` +
``lookup`` + ``has_permission`` + ``get_permissions`` path against it.
"""

from typing import AsyncIterator

import pytest

import uuid
from tests.doubles import _DummyUserContext as UserContext
from src.api import ObjectRef, Relationship, SubjectRef, UNDEFINED
from src.db.repos.permissions.spicedb_repo import SpicedbPermissionRepo


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]


# Local alias kept for backwards compatibility with the
# ``async def test_note_insert_and_check(note_permissions_repo)``
# signatures that pre-date the fixture rename.
@pytest.fixture(scope="function")
async def note_permissions_repo(
    spicedb_permission_repo: AsyncIterator[SpicedbPermissionRepo],
) -> AsyncIterator[SpicedbPermissionRepo]:
    """Alias around the canonical ``spicedb_permission_repo`` fixture.

    Kept here so existing test bodies keep using
    ``note_permissions_repo`` even though the new fixture is named
    ``spicedb_permission_repo``.
    """
    async for repo in spicedb_permission_repo:
        yield repo


async def test_note_insert_and_check(spicedb_permission_repo: SpicedbPermissionRepo):
    emilia = SubjectRef(object_type="user", object_id="emilia")
    alfred = SubjectRef(object_type="user", object_id="alfred")

    note_id = f"note-{uuid.uuid4().hex}"
    note = ObjectRef(object_type="note", object_id=note_id)

    relationships = [
        Relationship(resource=note, relation="admin", subject=emilia),
        Relationship(resource=note, relation="reader", subject=alfred),
    ]

    inserted = await spicedb_permission_repo.insert(relationships)
    assert len(inserted) == 2

    emilia_notes = await spicedb_permission_repo.lookup(
        Relationship(
            resource=ObjectRef(object_type="note", object_id=UNDEFINED),
            relation="view",
            subject=SubjectRef(object_type="user", object_id="emilia"),
        )
    )
    assert emilia_notes == [note_id]

    alfred_notes = await spicedb_permission_repo.lookup(
        Relationship(
            resource=ObjectRef(object_type="note", object_id=UNDEFINED),
            relation="view",
            subject=SubjectRef(object_type="user", object_id="alfred"),
        )
    )
    assert alfred_notes == [note_id]

    alfred_admin_notes = await spicedb_permission_repo.lookup(
        Relationship(
            resource=ObjectRef(object_type="note", object_id=UNDEFINED),
            relation="admin",
            subject=SubjectRef(object_type="user", object_id="alfred"),
        )
    )
    assert alfred_admin_notes == []


async def test_note_missing_permissions(spicedb_permission_repo: SpicedbPermissionRepo):
    emilia = SubjectRef(object_type="user", object_id="emilia")
    alfred = SubjectRef(object_type="user", object_id="alfred")

    reader_note_id = f"note-{uuid.uuid4().hex}"
    admin_note_id = f"note-{uuid.uuid4().hex}"
    reader_note = ObjectRef(object_type="note", object_id=reader_note_id)
    admin_note = ObjectRef(object_type="note", object_id=admin_note_id)

    inserted = await spicedb_permission_repo.insert(
        [
            Relationship(resource=reader_note, relation="reader", subject=emilia),
            Relationship(resource=admin_note, relation="admin", subject=alfred),
        ]
    )
    assert len(inserted) == 2

    # Emilia can view her reader note, but is not admin there.
    assert await spicedb_permission_repo.has_permission(
        UserContext("emilia"), "view", reader_note
    )
    assert not await spicedb_permission_repo.has_permission(
        UserContext("emilia"), "admin", reader_note
    )

    # Emilia has no permissions at all on Alfred's note.
    assert not await spicedb_permission_repo.has_permission(
        UserContext("emilia"), "view", admin_note
    )
    assert not await spicedb_permission_repo.has_permission(
        UserContext("emilia"), "admin", admin_note
    )
    assert await spicedb_permission_repo.get_permissions(
        UserContext("emilia"), admin_note
    ) == []


async def test_role_userset_roundtrip_and_expansion(spicedb_permission_repo: SpicedbPermissionRepo):
    """Stage 2+3: role usersets keep ``#member`` and expand to members."""
    note = ObjectRef(object_type="note", object_id=f"note-{uuid.uuid4().hex}")
    role = ObjectRef(object_type="role", object_id=f"role-{uuid.uuid4().hex}")

    await spicedb_permission_repo.insert([
        Relationship(
            resource=note,
            relation="reader",
            subject=SubjectRef(
                object_type="role",
                object_id=str(role.object_id),
                optional_relation="member",
            ),
        ),
        Relationship(
            resource=role,
            relation="member",
            subject=SubjectRef(object_type="user", object_id="alice"),
        ),
    ])

    stored = await spicedb_permission_repo.list_relationships(note)
    assert len(stored) == 1
    assert stored[0].subject.optional_relation == "member"

    assert await spicedb_permission_repo.has_permission(
        UserContext("alice"), "view", note
    )


async def test_capability_checks_against_real_schema(spicedb_permission_repo: SpicedbPermissionRepo):
    """Stage 3+4: the canonical schema enforces the capability matrix."""
    from src.api.other.relationship import ObjectTypeEnum
    from src.domain.share_policy import GrantPermissionFactory

    note = ObjectRef(object_type="note", object_id=f"note-{uuid.uuid4().hex}")
    await spicedb_permission_repo.insert([
        Relationship(resource=note, relation="owner", subject=SubjectRef("user", "owner")),
        Relationship(resource=note, relation="admin", subject=SubjectRef("user", "admin")),
        Relationship(resource=note, relation="writer", subject=SubjectRef("user", "writer")),
    ])
    factory = GrantPermissionFactory()

    async def allowed(user_id: str, relation: str) -> bool:
        chain = factory.for_relation(
            ObjectTypeEnum.NOTE, str(note.object_id), relation
        ).set_permission_repo(spicedb_permission_repo)
        return bool(await chain.check(UserContext(user_id)))

    # owner may grant admin; admin may not.
    assert await allowed("owner", "admin")
    assert not await allowed("admin", "admin")

    # admin may grant read/write; writer may not.
    assert await allowed("admin", "reader")
    assert await allowed("admin", "writer")
    assert not await allowed("writer", "reader")


async def test_directory_owner_implies_view_write_delete(spicedb_permission_repo: SpicedbPermissionRepo):
    """Stage 5+3: an owner-only directory is fully usable."""
    directory = ObjectRef(
        object_type="directory", object_id=f"dir-{uuid.uuid4().hex}"
    )
    await spicedb_permission_repo.insert([
        Relationship(
            resource=directory,
            relation="owner",
            subject=SubjectRef(object_type="user", object_id="owner"),
        )
    ])

    for permission in ("view", "write", "delete", "has_admin"):
        assert await spicedb_permission_repo.has_permission(
            UserContext("owner"), permission, directory
        ), permission


async def test_full_capability_matrix_against_real_schema(
    spicedb_permission_repo: SpicedbPermissionRepo,
):
    """Stage 4+3: every access relation is authorized against real SpiceDB."""
    from src.api.other.relationship import ObjectTypeEnum
    from src.domain.share_policy import GrantPermissionFactory

    matrix = {
        "reader": set(),
        "writer": set(),
        "admin": {
            "reader", "writer", "grant_read", "grant_write",
            "delegate_read", "delegate_write",
        },
        "owner": {
            "reader", "writer", "admin",
            "grant_read", "grant_write", "grant_admin",
            "delegate_read", "delegate_write", "delegate_admin",
        },
        "grant_read": {"reader"},
        "grant_write": {"writer"},
        "grant_admin": {"admin"},
        "delegate_read": {"reader", "grant_read"},
        "delegate_write": {"writer", "grant_write"},
        "delegate_admin": {"admin", "grant_admin"},
    }
    factory = GrantPermissionFactory()

    for resource_type in (
        ObjectTypeEnum.NOTE,
        ObjectTypeEnum.DIRECTORY,
        ObjectTypeEnum.SHELF,
    ):
        for actor_relation in matrix:
            actor_id = f"actor-{resource_type}-{actor_relation}"
            resource = ObjectRef(
                object_type=str(resource_type),
                object_id=f"{resource_type}-{uuid.uuid4().hex}",
            )
            await spicedb_permission_repo.insert([
                Relationship(
                    resource=resource,
                    relation=actor_relation,
                    subject=SubjectRef("user", actor_id),
                )
            ])
            for target_relation in matrix["owner"]:
                chain = factory.for_relation(
                    resource_type,
                    str(resource.object_id),
                    target_relation,
                ).set_permission_repo(spicedb_permission_repo)
                result = bool(await chain.check(UserContext(actor_id)))
                assert result is (target_relation in matrix[actor_relation]), (
                    f"{resource_type} actor={actor_relation} "
                    f"target={target_relation}"
                )


async def test_full_smoke_grpc_share_unshare_to_user_and_role(
    spicedb_permission_repo: SpicedbPermissionRepo,
):
    """Full smoke: gRPC note/dir/shelf x user/role -> SpiceDB -> audit."""
    from src.grpc_mod.permission_service import GrpcPermissionService
    from src.grpc_mod.proto.note_pb2 import (
        CreatePermissionRequest,
        DeletePermissionRequest,
        PermissionObjectType,
        PermissionRelationship,
        PermissionSubject,
    )
    from src.services.permissions import PermissionServiceImpl
    from tests.doubles import FakePermissionAuditRepo, _StubUserContextFactory
    from tests.doubles.stubs.permission_service import (
        _StubPermissionDirectoryRepo,
        _StubPermissionNoteRepo,
    )
    from tests.doubles.stubs.permission_targets import (
        _StubPermissionRoleRepo,
        _StubPermissionShelfRepo,
    )

    note_id, directory_id, shelf_id = (
        f"note-{uuid.uuid4().hex}",
        f"dir-{uuid.uuid4().hex}",
        f"shelf-{uuid.uuid4().hex}",
    )
    actor = "owner"
    await spicedb_permission_repo.insert([
        Relationship(ObjectRef("note", note_id), "owner", SubjectRef("user", actor)),
        Relationship(ObjectRef("directory", directory_id), "owner", SubjectRef("user", actor)),
        Relationship(ObjectRef("shelf", shelf_id), "owner", SubjectRef("user", actor)),
    ])

    audit = FakePermissionAuditRepo()
    service = PermissionServiceImpl(
        permission_repo=spicedb_permission_repo,
        note_repo=_StubPermissionNoteRepo({note_id}),
        directory_repo=_StubPermissionDirectoryRepo({directory_id}),
        shelf_repo=_StubPermissionShelfRepo({shelf_id}),
        role_repo=_StubPermissionRoleRepo(set()),
        audit_repo=audit,
    )
    grpc_service = GrpcPermissionService(
        permission_service=service,
        log=lambda name, instance: None,
        to_grpc=object(),
        context_factory=_StubUserContextFactory(),
    )

    cases = [
        (PermissionObjectType.PERMISSION_OBJECT_TYPE_NOTE, note_id),
        (PermissionObjectType.PERMISSION_OBJECT_TYPE_DIRECTORY, directory_id),
        (PermissionObjectType.PERMISSION_OBJECT_TYPE_SHELF, shelf_id),
    ]
    for object_type, object_id in cases:
        for subject_type, subject_id, optional in (
            (PermissionObjectType.PERMISSION_OBJECT_TYPE_USER, "alice", ""),
            (PermissionObjectType.PERMISSION_OBJECT_TYPE_ROLE, "role-1", "member"),
        ):
            relationship = PermissionRelationship(
                relation="reader",
                subject=PermissionSubject(
                    object_type=subject_type,
                    object_id=subject_id,
                    optional_relation=optional,
                ),
            )
            await grpc_service.CreatePermission(
                CreatePermissionRequest(
                    object_type=object_type,
                    object_id=object_id,
                    relationship=relationship,
                    user_id=actor,
                ),
                None,
            )
            await grpc_service.DeletePermission(
                DeletePermissionRequest(
                    object_type=object_type,
                    object_id=object_id,
                    relationship=relationship,
                    user_id=actor,
                ),
                None,
            )

    assert len(audit.entries) == 12
    assert {entry.action for entry in audit.entries} == {"grant", "revoke"}
    role_entries = [
        entry for entry in audit.entries if entry.subject_type == "role"
    ]
    assert len(role_entries) == 6
    assert all(entry.subject_relation == "member" for entry in role_entries)


async def test_permission_audit_repo_persists_rows(spicedb_postgres_env):
    """Stage 8+3: the audit table and repo round-trip real Postgres."""
    from src.db.entities.permission_audit import (
        FilterPermissionAudit,
        PermissionAuditEntity,
    )
    from src.db.repos.permissions.postgres_audit import (
        PostgresPermissionAuditRepo,
    )
    from src.db.table import Table
    from src.utils import logging_provider

    env = spicedb_postgres_env
    audit_repo = PostgresPermissionAuditRepo(
        table=Table(
            db=env.db,
            table_name="permission_audit",
            id_fields=["id"],
            logging_provider=logging_provider,
        ),
        logging_provider=logging_provider,
    )
    await audit_repo.record_many([
        PermissionAuditEntity(
            actor_id=None,
            accessed_as="system",
            action="grant",
            resource_type="note",
            resource_id="note-1",
            relation="reader",
            subject_type="role",
            subject_id="role-1",
            subject_relation="member",
            metadata={"level": "read"},
        )
    ])

    rows = await audit_repo.get(
        FilterPermissionAudit(resource_type="note", resource_id="note-1")
    )
    assert len(rows) == 1
    assert rows[0].subject_type == "role"
    assert rows[0].subject_relation == "member"
    assert rows[0].metadata == {"level": "read"}
