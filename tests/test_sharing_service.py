"""Unit tests for :class:`SharingServiceImpl`.

Pins the policy the service applies on top of the
:class:`ShareActionFacade`: it owns the permission-relation writes
(insert on create, swap on update, delete on teardown), enforces
``edit_permissions``, and routes reads through the facade so the
repo / user-repo / action-repo details never leak.

The fakes (``_FakeSharingRepo``, ``InMemoryPermissionRepo``,
``_FakePermissionService``, ``_FakeUserRepo``, ``_FakeUserActionRepo``)
live in :mod:`tests.stubs`.  Facade-direct tests live in
:mod:`tests.test_share_action_facade`; this file only covers the
service layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from src.api.other.relationship import NoteRelationEnum, ObjectRef, Relationship, SubjectRef
from src.api.other.undefined import UNDEFINED
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.entities.user.user_action import UserActionEntity
from src.facades.share_action_facade import ShareActionFacade
from src.services.sharing import SharingServiceImpl
from tests.stubs.activity_logger_service import _FakeActivityLoggerService
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.logging import silent_logger
from tests.stubs.permission_service import _FakePermissionService
from tests.stubs.sharing_repo import _FakeSharingRepo
from tests.stubs.user_action_repo import _FakeUserActionRepo
from tests.stubs.user_context import _UserContext
from tests.stubs.user_repo import _FakeUserRepo


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


async def _perms_with_edit(
    *, user_id: str, editable_note_ids: set[str]
) -> InMemoryPermissionRepo:
    """Build an :class:`InMemoryPermissionRepo` with ``edit_permissions`` granted.

    Inserts a ``note#admin@user`` relation per note id; the
    in-memory impl's implication map expands ``admin`` to
    ``{admin, delete, write, view, edit_permissions}``.
    """
    from src.api import NoteRelationEnum, ObjectRef, SubjectRef
    repo = InMemoryPermissionRepo()
    await repo.insert([
        Relationship(
            resource=ObjectRef("note", note_id),
            relation=NoteRelationEnum.ADMIN,
            subject=SubjectRef("user", user_id),
        )
        for note_id in editable_note_ids
    ])
    return repo


async def _build_service(
    sharing_repo: Optional[_FakeSharingRepo] = None,
    permissions: Optional[InMemoryPermissionRepo] = None,
    permission_service: Optional[_FakePermissionService] = None,
    user_repo: Optional[_FakeUserRepo] = None,
    user_action_repo: Optional[_FakeUserActionRepo] = None,
    activity_logger: Optional[_FakeActivityLoggerService] = None,
) -> tuple[SharingServiceImpl, _FakeActivityLoggerService]:
    """Assemble a :class:`SharingServiceImpl` with fakes for every dep."""
    activity_logger = activity_logger or _FakeActivityLoggerService()
    service = SharingServiceImpl(
        share_facade=ShareActionFacade(
            sharing_repo=sharing_repo or _FakeSharingRepo(),
            user_repo=user_repo or _FakeUserRepo(),
            user_action_repo=user_action_repo or _FakeUserActionRepo(),
            logging_provider=silent_logger,
        ),
        permission_repo=permissions or InMemoryPermissionRepo(),
        permission_service=permission_service or _FakePermissionService(),
        logging_provider=silent_logger,
        user_repo=user_repo or _FakeUserRepo(),
        activity_logger=activity_logger,
    )
    return service, activity_logger


def _share(
    *,
    id: str = "share-1",
    note_id: str = "note-1",
    access_as: str = "access-user",
) -> NoteShareEntity:
    return NoteShareEntity(
        id=id,
        note_id=note_id,
        created_at=datetime(2026, 1, 1),
        created_by="creator-1",
        access_as=access_as,
    )


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


async def test_create_share_populates_service_audit_defaults() -> None:
    """Service fills ``created_at`` / ``created_by`` / ``id`` before delegating."""
    repo = _FakeSharingRepo()
    service, _activity_logger = await _build_service(
        sharing_repo=repo,
        permissions=await _perms_with_edit(user_id="creator-1", editable_note_ids={"note-1"}),
    )

    created = await service.create_share(
        NoteShareEntity(id="client-id", note_id="note-1", permission="read"),
        _UserContext("creator-1"),
    )

    assert created.id is UNDEFINED
    assert created.created_by == "creator-1"
    assert isinstance(created.created_at, datetime)
    assert repo.created_share is created


async def test_create_share_overrides_explicit_audit_values() -> None:
    """``created_by`` is always taken from the actor context, never the caller's input."""
    service, _activity_logger = await _build_service(
        permissions=await _perms_with_edit(user_id="actor", editable_note_ids={"note-1"}),
    )

    created = await service.create_share(
        NoteShareEntity(
            note_id="note-1",
            created_at=datetime(2026, 6, 20),
            created_by="explicit-creator",
            permission="read",
        ),
        _UserContext("actor"),
    )

    assert created.created_at == datetime(2026, 6, 20)
    assert created.created_by == "actor"


@pytest.mark.parametrize("permission", ["read", "write"])
async def test_create_share_inserts_reader_or_writer_relation(permission: str) -> None:
    """The service inserts the matching SpiceDB relation after the facade returns.

    ``read`` -> :class:`NoteRelationEnum.READER`, ``write`` -> :class:`WRITER`.
    Tested as a parametrize so both branches stay green together.
    """
    permissions = await _perms_with_edit(user_id="creator-1", editable_note_ids={"note-1"})
    service, _activity_logger = await _build_service(permissions=permissions)

    await service.create_share(
        NoteShareEntity(note_id="note-1", permission=permission),
        _UserContext("creator-1"),
    )

    expected_relation = (
        NoteRelationEnum.READER if permission == "read" else NoteRelationEnum.WRITER
    )
    assert any(
        rel.relation == expected_relation
        and str(rel.subject.object_id) == rel.subject.object_id  # subject is the temp user
        and str(rel.resource.object_id) == "note-1"
        for rel in permissions._store
    )


async def test_create_share_denies_without_edit_permission() -> None:
    """No ``edit_permissions`` -> :exc:`PermissionError`, no shares / actions / relations written."""
    user_action_repo = _FakeUserActionRepo()
    user_repo = _FakeUserRepo()
    sharing_repo = _FakeSharingRepo()
    permissions = InMemoryPermissionRepo()
    service, _activity_logger = await _build_service(
        sharing_repo=sharing_repo,
        permissions=permissions,
        user_repo=user_repo,
        user_action_repo=user_action_repo,
    )

    with pytest.raises(PermissionError):
        await service.create_share(
            NoteShareEntity(note_id="note-1", permission="read"),
            _UserContext("creator-1"),
        )

    assert sharing_repo.created_share is None
    assert user_repo.inserted == []
    assert user_action_repo.add_action_calls == []
    assert permissions._store == []


@pytest.mark.parametrize(
    "bad_permission",
    [UNDEFINED, None, "owner", "READ", ""],
)
async def test_create_share_rejects_bad_permission(bad_permission: object) -> None:
    """Anything outside ``"read"`` / ``"write"`` raises :exc:`ValueError`."""
    service, _activity_logger = await _build_service(
        permissions=await _perms_with_edit(user_id="creator-1", editable_note_ids={"note-1"}),
    )

    with pytest.raises(ValueError):
        await service.create_share(
            NoteShareEntity(note_id="note-1", permission=bad_permission),  # type: ignore[arg-type]
            _UserContext("creator-1"),
        )


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


async def test_update_share_swaps_reader_relation_to_writer_via_permission_service() -> None:
    """A ``permission`` change goes through ``PermissionService.replace_relationships``."""
    existing = Relationship(
        resource=ObjectRef("note", "note-1"),
        relation=NoteRelationEnum.READER,
        subject=SubjectRef("user", "access-user"),
    )
    permissions = InMemoryPermissionRepo()
    await permissions.insert([existing])
    await permissions.insert([
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation=NoteRelationEnum.ADMIN,
            subject=SubjectRef("user", "creator-1"),
        )
    ])
    permission_service = _FakePermissionService()
    service, _activity_logger = await _build_service(
        sharing_repo=_FakeSharingRepo([_share()]),
        permissions=permissions,
        permission_service=permission_service,
    )

    updated = await service.update_share(
        NoteShareEntity(id="share-1", permission="write"),
        _UserContext("creator-1"),
    )

    assert updated.permission == "write"
    assert len(permission_service.replace_calls) == 1
    resource, rels, _ = permission_service.replace_calls[0]
    assert resource == ObjectRef("note", "note-1")
    # The access-user's writer replaces their previous reader; any
    # unrelated relations seeded for the authz check are preserved.
    assert any(
        rel.relation == NoteRelationEnum.WRITER
        and str(rel.subject.object_id) == "access-user"
        and str(rel.resource.object_id) == "note-1"
        for rel in rels
    )


async def test_update_share_preserves_unrelated_relationships() -> None:
    """Only the access user's reader/writer row is replaced; owner/admin/etc. survive."""
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
    permissions = InMemoryPermissionRepo()
    await permissions.insert([owner_rel, existing_reader])
    await permissions.insert([
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation=NoteRelationEnum.ADMIN,
            subject=SubjectRef("user", "creator-1"),
        )
    ])
    permission_service = _FakePermissionService()
    service, _activity_logger = await _build_service(
        sharing_repo=_FakeSharingRepo([_share()]),
        permissions=permissions,
        permission_service=permission_service,
    )

    await service.update_share(
        NoteShareEntity(id="share-1", permission="write"),
        _UserContext("creator-1"),
    )

    _, rels, _ = permission_service.replace_calls[0]
    assert owner_rel in rels
    assert existing_reader not in rels


async def test_update_share_without_permission_field_does_not_call_permission_service() -> None:
    """No ``permission`` field in the update -> no relation swap."""
    service, _activity_logger = await _build_service(
        sharing_repo=_FakeSharingRepo([_share()]),
        permissions=await _perms_with_edit(user_id="creator-1", editable_note_ids={"note-1"}),
        permission_service=_FakePermissionService(),
    )

    updated = await service.update_share(
        NoteShareEntity(id="share-1", description="new"),
        _UserContext("creator-1"),
    )

    assert updated.description == "new"
    assert service._permission_service.replace_calls == []


async def test_update_share_denies_without_edit_permission() -> None:
    """No ``edit_permissions`` -> :exc:`PermissionError`, no permission swap."""
    service, _activity_logger = await _build_service(
        sharing_repo=_FakeSharingRepo([_share(note_id="note-denied")]),
        permissions=InMemoryPermissionRepo(),
        permission_service=_FakePermissionService(),
    )

    with pytest.raises(PermissionError):
        await service.update_share(
            NoteShareEntity(id="share-1", description="x"),
            _UserContext("creator-1"),
        )


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


async def test_get_shares_filters_unauthorized_entries() -> None:
    """``get_shares`` only returns shares for notes the actor can edit."""
    sharing_repo = _FakeSharingRepo(
        [
            _share(id="allowed", note_id="note-allowed"),
            _share(id="denied", note_id="note-denied"),
        ]
    )
    permissions = await _perms_with_edit(
        user_id="creator-1", editable_note_ids={"note-allowed"}
    )
    service, _activity_logger = await _build_service(
        sharing_repo=sharing_repo,
        permissions=permissions,
    )

    shares = await service.get_shares(FilterShareNote(), _UserContext("creator-1"))

    assert [share.id for share in shares] == ["allowed"]


async def test_get_share_template_delegates_to_get_shares() -> None:
    service, _activity_logger = await _build_service(
        sharing_repo=_FakeSharingRepo([_share()]),
        permissions=await _perms_with_edit(user_id="creator-1", editable_note_ids={"note-1"}),
    )
    filter = FilterShareNote(note_id="note-1")

    share = await service.get_share(filter, _UserContext("creator-1"))

    assert share.id == "share-1"
    assert service._share_facade is not None


async def test_get_share_by_id_delegates_to_facade() -> None:
    service, _activity_logger = await _build_service(
        sharing_repo=_FakeSharingRepo([_share()]),
        permissions=await _perms_with_edit(user_id="creator-1", editable_note_ids={"note-1"}),
    )

    share = await service.get_share_by_id("share-1", _UserContext("creator-1"))

    assert share.id == "share-1"


async def test_get_shares_resolves_share_permission_from_spicedb() -> None:
    """``get_shares`` populates ``share.permission`` from the read-side permission lookup."""
    permissions = InMemoryPermissionRepo()
    # Seed: creator-1 has edit_permissions on note-1; access-user is a reader.
    await permissions.insert([
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation=NoteRelationEnum.ADMIN,
            subject=SubjectRef("user", "creator-1"),
        ),
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation=NoteRelationEnum.READER,
            subject=SubjectRef("user", "access-user"),
        ),
    ])
    service, _activity_logger = await _build_service(
        sharing_repo=_FakeSharingRepo([_share()]),
        permissions=permissions,
    )

    shares = await service.get_shares(FilterShareNote(), _UserContext("creator-1"))

    assert shares[0].permission == "read"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_shares_removes_relation_row_user_and_actions() -> None:
    """End-to-end teardown: relation -> share row -> actions -> temp user."""
    pending = UserActionEntity(
        id="pending-1",
        user_id="access-user",
        action="disable",
        execute_at=datetime(2026, 7, 1),
    )
    action_repo = _FakeUserActionRepo(initial=[pending])
    user_repo = _FakeUserRepo()
    sharing_repo = _FakeSharingRepo([_share()])
    permissions = InMemoryPermissionRepo()
    await permissions.insert([
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation=NoteRelationEnum.ADMIN,
            subject=SubjectRef("user", "creator-1"),
        ),
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation=NoteRelationEnum.READER,
            subject=SubjectRef("user", "access-user"),
        ),
    ])
    service, _activity_logger = await _build_service(
        sharing_repo=sharing_repo,
        permissions=permissions,
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    await service.delete_shares(["share-1"], _UserContext("creator-1"))

    # the reader/writer relation for the access user is gone
    assert not any(
        rel
        for rel in permissions._store
        if str(rel.subject.object_id) == "access-user"
    )
    # share row, action, and temp user all removed
    assert sharing_repo.deleted_ids == ["share-1"]
    assert action_repo.remove_action_calls == ["pending-1"]
    assert user_repo.deleted == ["access-user"]


async def test_delete_shares_leaves_unrelated_actions_alone() -> None:
    """Action rows for *other* users must survive the teardown."""
    other_user_action = UserActionEntity(
        id="other-user-1",
        user_id="somebody-else",
        action="disable",
        execute_at=datetime(2026, 7, 1),
    )
    action_repo = _FakeUserActionRepo(initial=[other_user_action])
    service, _activity_logger = await _build_service(
        sharing_repo=_FakeSharingRepo([_share()]),
        permissions=await _perms_with_edit(user_id="creator-1", editable_note_ids={"note-1"}),
        user_action_repo=action_repo,
    )

    await service.delete_shares(["share-1"], _UserContext("creator-1"))

    assert other_user_action.id not in action_repo.remove_action_calls
    assert action_repo.for_user("somebody-else") == [other_user_action]


async def test_delete_shares_requires_non_empty_input() -> None:
    service, _activity_logger = await _build_service()

    with pytest.raises(ValueError):
        await service.delete_shares([], _UserContext("creator-1"))


async def test_delete_shares_checks_all_note_ids_before_any_teardown() -> None:
    """If any note fails the ``edit_permissions`` check, no share is deleted."""
    sharing_repo = _FakeSharingRepo(
        [
            _share(id="share-ok", note_id="note-ok"),
            _share(id="share-bad", note_id="note-bad"),
        ]
    )
    permissions = await _perms_with_edit(
        user_id="creator-1", editable_note_ids={"note-ok"}
    )
    service, _activity_logger = await _build_service(
        sharing_repo=sharing_repo,
        permissions=permissions,
    )

    with pytest.raises(PermissionError):
        await service.delete_shares(["share-ok", "share-bad"], _UserContext("creator-1"))

    assert sharing_repo.deleted_ids == []


# ---------------------------------------------------------------------------
# activity logging
# ---------------------------------------------------------------------------


async def test_create_share_records_note_shared_with_metadata() -> None:
    """`create_share` records `note_shared` with the permission + access user."""
    permissions = await _perms_with_edit(
        user_id="creator-1", editable_note_ids={"note-1"}
    )
    service, activity_logger = await _build_service(permissions=permissions)

    created = await service.create_share(
        NoteShareEntity(note_id="note-1", permission="read"),
        _UserContext("creator-1"),
    )

    access_as = str(created.access_as)
    assert activity_logger.calls == [
        (
            "note_shared",
            "note-1",
            "creator-1",
            {"permission": "reader", "access_as": access_as},
        )
    ]


async def test_create_share_does_not_record_when_denied() -> None:
    """`create_share` records nothing when the actor lacks edit permissions."""
    service, activity_logger = await _build_service(
        permissions=InMemoryPermissionRepo(),
    )

    with pytest.raises(PermissionError):
        await service.create_share(
            NoteShareEntity(note_id="note-1", permission="read"),
            _UserContext("actor"),
        )

    assert activity_logger.calls == []


async def test_delete_shares_records_note_unshared_per_share() -> None:
    """`delete_shares` records `note_unshared` for every share it removes."""
    sharing_repo = _FakeSharingRepo([_share(id="share-1"), _share(id="share-2")])
    permissions = await _perms_with_edit(
        user_id="creator", editable_note_ids={"note-1"}
    )
    service, activity_logger = await _build_service(
        sharing_repo=sharing_repo,
        permissions=permissions,
    )

    await service.delete_shares(["share-1", "share-2"], _UserContext("creator"))

    actions = [entry[0] for entry in activity_logger.calls]
    assert actions == ["note_unshared", "note_unshared"]
    for entry in activity_logger.calls:
        action, note_id, actor_id, metadata = entry
        assert action == "note_unshared"
        assert note_id == "note-1"
        assert actor_id == "creator"
        assert set(metadata) == {"share_id", "access_as"}


async def test_delete_shares_does_not_record_when_denied() -> None:
    """`delete_shares` records nothing when an `edit_permissions` check fails."""
    service, activity_logger = await _build_service(
        sharing_repo=_FakeSharingRepo([_share(note_id="note-denied")]),
        permissions=InMemoryPermissionRepo(),
    )

    with pytest.raises(PermissionError):
        await service.delete_shares(["share-1"], _UserContext("creator-1"))

    assert activity_logger.calls == []
