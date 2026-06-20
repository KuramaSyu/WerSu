from datetime import datetime

import pytest

from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
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

    def __init__(self, editable_note_ids: set[str]) -> None:
        self.editable_note_ids = editable_note_ids
        self.checked_note_ids: list[str] = []

    async def has_permission(self, user, permission: str, resource) -> bool:
        self.checked_note_ids.append(str(resource.object_id))
        return permission == "edit_permissions" and resource.object_id in self.editable_note_ids


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
    service = DefaultSharingService(repo, permissions)

    created = await service.create_share(
        NoteShareEntity(id="client-id", note_id="note-1", access_as="access-user"),
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
    service = DefaultSharingService(repo, permissions)
    created_at = datetime(2026, 6, 20)

    created = await service.create_share(
        NoteShareEntity(
            note_id="note-1",
            created_at=created_at,
            created_by="explicit-creator",
            access_as="access-user",
        ),
        _UserContext("actor"),
    )

    assert created.created_at == created_at
    assert created.created_by == "explicit-creator"


async def test_get_shares_filters_unauthorized_entries() -> None:
    """Read searches return only shares for notes the actor can manage."""
    repo = _FakeSharingRepo(
        [
            _share(id="allowed", note_id="note-allowed"),
            _share(id="denied", note_id="note-denied"),
        ]
    )
    permissions = _FakePermissionRepo(editable_note_ids={"note-allowed"})
    service = DefaultSharingService(repo, permissions)

    shares = await service.get_shares(FilterShareNote(), _UserContext())

    assert [share.id for share in shares] == ["allowed"]
    assert permissions.checked_note_ids == ["note-allowed", "note-denied"]


async def test_get_share_template_uses_get_shares() -> None:
    """Single filtered fetches should flow through the plural fetch path."""
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = DefaultSharingService(repo, permissions)
    filter = FilterShareNote(note_id="note-1")

    share = await service.get_share(filter, _UserContext())

    assert share.id == "share-1"
    assert repo.get_shares_calls == [filter]


async def test_get_share_by_id_template_uses_get_shares_by_id() -> None:
    """Single ID fetches should flow through the plural ID fetch path."""
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = DefaultSharingService(repo, permissions)

    share = await service.get_share_by_id("share-1", _UserContext())

    assert share.id == "share-1"
    assert repo.get_shares_by_id_calls == [["share-1"]]


async def test_update_share_denies_without_edit_permission() -> None:
    """Mutations stay strict and raise when the actor cannot edit the note."""
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-denied")])
    permissions = _FakePermissionRepo(editable_note_ids=set())
    service = DefaultSharingService(repo, permissions)

    with pytest.raises(PermissionError):
        await service.update_share(NoteShareEntity(id="share-1", description="x"), _UserContext())


async def test_delete_shares_denies_without_edit_permission() -> None:
    """Deleting any share requires edit permission on its note."""
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-denied")])
    permissions = _FakePermissionRepo(editable_note_ids=set())
    service = DefaultSharingService(repo, permissions)

    with pytest.raises(PermissionError):
        await service.delete_shares(["share-1"], _UserContext())

    assert repo.deleted_ids is None
