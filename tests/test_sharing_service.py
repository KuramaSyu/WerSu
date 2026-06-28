from datetime import datetime
import logging

import pytest

from src.api.relationship import NoteRelationEnum, ObjectRef, Relationship, SubjectRef
from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.services.permissions import PermissionServiceABC
from src.services.sharing import DefaultSharingService


class _UserContext(UserContextABC):
    """Small user context for service tests."""

    def __init__(self, user_id: str = "actor") -> None:
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id


class _FakeSharingRepo:
    """In-memory sharing repo that records calls made by the service."""

    def __init__(self, shares: list[NoteShareEntity] | None = None) -> None:
        self.shares = shares or []
        self.created_share = None
        self.updated_share = None
        self.deleted_ids = None
        self.last_filter = None
        self.get_shares_by_id_calls: list[list[str]] = []
        self.get_shares_calls: list[FilterShareNote] = []

    async def create_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        self.created_share = share
        return share

    async def update_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        self.updated_share = share
        return share

    async def get_share_by_id(self, share_id: str, ctx: UserContextABC) -> NoteShareEntity:
        shares = await self.get_shares_by_id([share_id], ctx)
        if not shares:
            raise ValueError(f"Share not found: {share_id}")
        return shares[0]

    async def get_shares_by_id(
        self,
        share_ids: list[str],
        ctx: UserContextABC,
    ) -> list[NoteShareEntity]:
        self.get_shares_by_id_calls.append(share_ids)
        return [share for share in self.shares if share.id in share_ids]

    async def get_shares(
        self,
        filter: FilterShareNote,
        ctx: UserContextABC,
    ) -> list[NoteShareEntity]:
        self.last_filter = filter
        self.get_shares_calls.append(filter)
        return self.shares

    async def delete_shares(self, share_ids: list[str], ctx: UserContextABC) -> None:
        self.deleted_ids = share_ids


class _FakePermissionRepo:
    """Permission fake that grants edit access for selected note IDs."""

    def __init__(
        self,
        editable_note_ids: set[str],
        permissions_by_access_user: dict[tuple[str, str], list[str]] | None = None,
        stored_relationships: list[Relationship] | None = None,
    ) -> None:
        self.editable_note_ids = editable_note_ids
        self.checked_note_ids: list[str] = []
        self._relationships: list[Relationship] = list(stored_relationships or [])
        # (note_id, access_as) -> effective permission strings
        self._permissions_by_access_user: dict[tuple[str, str], list[str]] = (
            permissions_by_access_user or {}
        )

    async def has_permission(self, user, permission: str, resource) -> bool:
        self.checked_note_ids.append(str(resource.object_id))
        return permission == "edit_permissions" and resource.object_id in self.editable_note_ids

    async def insert(self, relationships: list[Relationship]) -> list[Relationship]:
        for rel in relationships:
            self._relationships.append(rel)
        return list(relationships)

    async def delete(self, relationship: Relationship) -> Relationship:
        self._relationships = [
            rel for rel in self._relationships
            if not (
                str(rel.resource.object_type) == str(relationship.resource.object_type)
                and str(rel.resource.object_id) == str(relationship.resource.object_id)
                and str(rel.relation) == str(relationship.relation)
                and str(rel.subject.object_type) == str(relationship.subject.object_type)
                and str(rel.subject.object_id) == str(relationship.subject.object_id)
            )
        ]
        return relationship

    async def list_relationships(self, resource: ObjectRef) -> list[Relationship]:
        return [
            rel for rel in self._relationships
            if str(rel.resource.object_type) == str(resource.object_type)
            and str(rel.resource.object_id) == str(resource.object_id)
        ]

    async def get_permissions(self, user, resource) -> list[str]:
        return list(
            self._permissions_by_access_user.get(
                (str(resource.object_id), str(user.user_id)),
                [],
            )
        )


class _FakePermissionService(PermissionServiceABC):
    """Permission service fake that records replace_relationships calls."""

    def __init__(self) -> None:
        self.replace_calls: list[tuple[ObjectRef, list[Relationship], UserContextABC]] = []

    async def list_relationships(self, resource, actor):
        raise NotImplementedError()

    async def create_relationship(self, relationship, actor):
        raise NotImplementedError()

    async def delete_relationship(self, relationship, actor):
        raise NotImplementedError()

    async def replace_relationships(self, resource, relationships, actor):
        self.replace_calls.append((resource, list(relationships), actor))
        return list(relationships)


class _FakeUserRepo:
    """Minimal user repo stub for create_share tests."""

    async def insert(self, user: UserEntity) -> UserEntity:
        return user


def _silent_logger(name: str, owner=None) -> logging.Logger:
    return logging.getLogger(name)


def _build_service(
    repo: _FakeSharingRepo,
    permissions: _FakePermissionRepo,
    permission_service: _FakePermissionService | None = None,
) -> DefaultSharingService:
    return DefaultSharingService(
        sharing_repo=repo,
        user_repo=_FakeUserRepo(),
        permission_repo=permissions,
        permission_service=permission_service or _FakePermissionService(),
        logging_provider=_silent_logger,
    )


def _share(
    id: str = "share-1",
    note_id: str = "note-1",
    access_as: str = "access-user",
) -> NoteShareEntity:
    """Create a complete share entity for service tests."""
    return NoteShareEntity(
        id=id,
        note_id=note_id,
        created_at=datetime(2026, 1, 1),
        created_by="creator-1",
        access_as=access_as,
    )


async def test_create_share_sets_defaults_in_service() -> None:
    """Service owns application defaults before delegating persistence."""
    repo = _FakeSharingRepo()
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(repo, permissions)

    created = await service.create_share(
        NoteShareEntity(id="client-id", note_id="note-1", access_as="access-user", permission="read"),
        _UserContext("creator-1"),
    )

    assert created.id is UNDEFINED
    assert created.created_by == "creator-1"
    assert isinstance(created.created_at, datetime)
    assert repo.created_share is created


async def test_create_share_keeps_explicit_audit_values() -> None:
    """Explicit audit values should not be overwritten by defaults."""
    repo = _FakeSharingRepo()
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(repo, permissions)
    created_at = datetime(2026, 6, 20)

    created = await service.create_share(
        NoteShareEntity(
            note_id="note-1",
            created_at=created_at,
            created_by="explicit-creator",
            access_as="access-user",
            permission="read",
        ),
        _UserContext("actor"),
    )

    assert created.created_at == created_at
    # created_by is always taken from the actor context
    assert created.created_by == "actor"


async def test_get_shares_filters_unauthorized_entries() -> None:
    """Read searches return only shares for notes the actor can manage."""
    repo = _FakeSharingRepo(
        [
            _share(id="allowed", note_id="note-allowed"),
            _share(id="denied", note_id="note-denied"),
        ]
    )
    permissions = _FakePermissionRepo(editable_note_ids={"note-allowed"})
    service = _build_service(repo, permissions)

    shares = await service.get_shares(FilterShareNote(), _UserContext())

    assert [share.id for share in shares] == ["allowed"]
    assert permissions.checked_note_ids == ["note-allowed", "note-denied"]


async def test_get_share_template_uses_get_shares() -> None:
    """Single filtered fetches should flow through the plural fetch path."""
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(repo, permissions)
    filter = FilterShareNote(note_id="note-1")

    share = await service.get_share(filter, _UserContext())

    assert share.id == "share-1"
    assert repo.get_shares_calls == [filter]


async def test_get_share_by_id_template_uses_get_shares_by_id() -> None:
    """Single ID fetches should flow through the plural ID fetch path."""
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(repo, permissions)

    share = await service.get_share_by_id("share-1", _UserContext())

    assert share.id == "share-1"
    assert repo.get_shares_by_id_calls == [["share-1"]]


async def test_update_share_denies_without_edit_permission() -> None:
    """Mutations stay strict and raise when the actor cannot edit the note."""
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-denied")])
    permissions = _FakePermissionRepo(editable_note_ids=set())
    permission_service = _FakePermissionService()
    service = _build_service(repo, permissions, permission_service)

    with pytest.raises(PermissionError):
        await service.update_share(NoteShareEntity(id="share-1", description="x"), _UserContext())

    assert permission_service.replace_calls == []


async def test_delete_shares_denies_without_edit_permission() -> None:
    """Deleting any share requires edit permission on its note."""
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-denied")])
    permissions = _FakePermissionRepo(editable_note_ids=set())
    service = _build_service(repo, permissions)

    with pytest.raises(PermissionError):
        await service.delete_shares(["share-1"], _UserContext())

    assert repo.deleted_ids is None


async def test_update_share_replaces_permission_via_permission_service() -> None:
    """Changing the permission flows through permission_service.replace_relationships."""
    existing = Relationship(
        resource=ObjectRef("note", "note-1"),
        relation=NoteRelationEnum.READER,
        subject=SubjectRef("user", "access-user"),
    )
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1", access_as="access-user")])
    permissions = _FakePermissionRepo(
        editable_note_ids={"note-1"},
        stored_relationships=[existing],
    )
    permission_service = _FakePermissionService()
    service = _build_service(repo, permissions, permission_service)

    updated = await service.update_share(
        NoteShareEntity(id="share-1", permission="write"),
        _UserContext(),
    )

    assert updated.permission == "write"
    assert repo.updated_share is updated
    assert len(permission_service.replace_calls) == 1
    resource, rels, actor = permission_service.replace_calls[0]
    assert resource == ObjectRef("note", "note-1")
    assert rels == [
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation=NoteRelationEnum.WRITER,
            subject=SubjectRef("user", "access-user"),
        )
    ]


async def test_update_share_without_permission_does_not_touch_permission_service() -> None:
    """Updates that don't change the permission leave the permission store alone."""
    existing = Relationship(
        resource=ObjectRef("note", "note-1"),
        relation=NoteRelationEnum.READER,
        subject=SubjectRef("user", "access-user"),
    )
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1", access_as="access-user")])
    permissions = _FakePermissionRepo(
        editable_note_ids={"note-1"},
        stored_relationships=[existing],
    )
    permission_service = _FakePermissionService()
    service = _build_service(repo, permissions, permission_service)

    updated = await service.update_share(
        NoteShareEntity(id="share-1", description="new"),
        _UserContext(),
    )

    assert updated.description == "new"
    assert permission_service.replace_calls == []


async def test_update_share_preserves_unrelated_relationships() -> None:
    """Other note relations (e.g. owner) must be preserved when the share permission changes."""
    owner_rel = Relationship(
        resource=ObjectRef("note", "note-1"),
        relation=NoteRelationEnum.OWNER,
        subject=SubjectRef("user", "owner-1"),
    )
    existing_reader = Relationship(
        resource=ObjectRef("note", "note-1"),
        relation=NoteRelationEnum.READER,
        subject=SubjectRef("user", "access-user"),
    )
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1", access_as="access-user")])
    permissions = _FakePermissionRepo(
        editable_note_ids={"note-1"},
        stored_relationships=[owner_rel, existing_reader],
    )
    permission_service = _FakePermissionService()
    service = _build_service(repo, permissions, permission_service)

    await service.update_share(
        NoteShareEntity(id="share-1", permission="write"),
        _UserContext(),
    )

    resource, rels, _ = permission_service.replace_calls[0]
    assert resource == ObjectRef("note", "note-1")
    assert owner_rel in rels
    # the old reader entry is gone, replaced by a writer entry
    assert existing_reader not in rels
    assert Relationship(
        resource=ObjectRef("note", "note-1"),
        relation=NoteRelationEnum.WRITER,
        subject=SubjectRef("user", "access-user"),
    ) in rels
