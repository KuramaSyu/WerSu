"""Stage 5 test: directory owner backfill migration."""

from __future__ import annotations

import importlib.util

import pytest
from pathlib import Path
from types import SimpleNamespace

from src.api.other.undefined import UNDEFINED
from src.api.other.relationship import (
    DirectoryRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.db.migrations.context import MigrationContext, MigrationServices
from tests.doubles import InMemoryPermissionRepo


class _BackfillDb:
    def __init__(
        self,
        directory_ids: list[str],
        creators: dict[str, str],
    ) -> None:
        self._directory_ids = directory_ids
        self._creators = creators

    async def fetch(self, query: str, *args):
        if "FROM note.directory" in query:
            return [{"id": did} for did in self._directory_ids]
        if "directory_created" in query:
            return [
                {"directory_id": did, "actor_id": actor}
                for did, actor in self._creators.items()
            ]
        raise AssertionError(f"unexpected query: {query}")


def _load_migration():
    path = Path(
        "src/db/migrations/20261110-2-backfill-directory-owners.py"
    )
    spec = importlib.util.spec_from_file_location("directory_owner_backfill", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    return module.Migration(path, lambda name, instance: logger)


async def _owner_relations(repo: InMemoryPermissionRepo):
    return [
        rel for rel in await repo.list_relationships(
            ObjectRef(ObjectTypeEnum.DIRECTORY, UNDEFINED)
        )
        if str(rel.relation) == "owner"
    ]


async def test_backfill_uses_activity_then_admin_fallback_and_is_idempotent() -> None:
    repo = InMemoryPermissionRepo()
    await repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            relation=DirectoryRelationEnum.ADMIN,
            subject=SubjectRef(ObjectTypeEnum.USER, "alice"),
        ),
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
            relation=DirectoryRelationEnum.ADMIN,
            subject=SubjectRef(ObjectTypeEnum.USER, "bob"),
        ),
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-3"),
            relation=DirectoryRelationEnum.OWNER,
            subject=SubjectRef(ObjectTypeEnum.USER, "dave"),
        ),
    ])

    db = _BackfillDb(
        ["dir-1", "dir-2", "dir-3"],
        {"dir-1": "carol"},
    )
    migration = _load_migration()
    ctx = MigrationContext(
        db=db,
        services=MigrationServices(permission_repo=repo),
    )

    await migration.up(ctx)
    owners = {
        (str(rel.resource.object_id), str(rel.subject.object_id))
        for rel in await _owner_relations(repo)
    }
    assert owners == {
        ("dir-1", "carol"),
        ("dir-2", "bob"),
        ("dir-3", "dave"),
    }

    # Rerunnable: the owner-presence check skips every directory.
    await migration.up(ctx)
    owners_again = {
        (str(rel.resource.object_id), str(rel.subject.object_id))
        for rel in await _owner_relations(repo)
    }
    assert owners_again == owners


async def test_backfill_role_admin_is_not_promoted() -> None:
    repo = InMemoryPermissionRepo()
    await repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            relation=DirectoryRelationEnum.ADMIN,
            subject=SubjectRef(
                ObjectTypeEnum.ROLE, "role-1", optional_relation="member"
            ),
        ),
    ])
    db = _BackfillDb(["dir-1"], {})
    migration = _load_migration()
    ctx = MigrationContext(
        db=db,
        services=MigrationServices(permission_repo=repo),
    )

    await migration.up(ctx)
    assert await _owner_relations(repo) == []

async def test_backfilled_owner_can_remove_admin_and_admin_cannot_remove_owner() -> None:
    """Full-smoke: backfill then rank enforcement on the same directory."""
    from src.domain.share_policy import GrantPermissionFactory
    from src.services.permissions import PermissionServiceImpl
    from tests.doubles import _DummyUserContext
    from tests.doubles.stubs.permission_service import (
        _StubPermissionDirectoryRepo,
        _StubPermissionNoteRepo,
    )

    repo = InMemoryPermissionRepo()
    await repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            relation=DirectoryRelationEnum.ADMIN,
            subject=SubjectRef(ObjectTypeEnum.USER, "alice"),
        ),
    ])
    migration = _load_migration()
    await migration.up(
        MigrationContext(
            db=_BackfillDb(["dir-1"], {"dir-1": "carol"}),
            services=MigrationServices(permission_repo=repo),
        )
    )

    service = PermissionServiceImpl(
        permission_repo=repo,
        note_repo=_StubPermissionNoteRepo(set()),
        directory_repo=_StubPermissionDirectoryRepo({"dir-1"}),
        grant_factory=GrantPermissionFactory(),
    )

    # The admin cannot remove the owner (owner is never writable via the API).
    with pytest.raises(ValueError, match="owner"):
        await service.delete_relationship(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
                relation=DirectoryRelationEnum.OWNER,
                subject=SubjectRef(ObjectTypeEnum.USER, "carol"),
            ),
            _DummyUserContext("alice"),
        )

    # The backfilled owner can remove the admin.
    await service.delete_relationship(
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            relation=DirectoryRelationEnum.ADMIN,
            subject=SubjectRef(ObjectTypeEnum.USER, "alice"),
        ),
        _DummyUserContext("carol"),
    )
    remaining = {
        str(rel.relation) for rel in await repo.list_relationships(
            ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1")
        )
    }
    assert "admin" not in remaining
    assert "owner" in remaining
