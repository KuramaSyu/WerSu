"""Unit tests for :class:`DefaultSharingService`.

The fakes used here (``_FakeSharingRepo``, ``_FakePermissionRepo``,
``_FakePermissionService``, ``_FakeUserRepo``, ``_FakeUserActionRepo``)
live in :mod:`tests.stubs.sharing`.  They are intentionally thin but
rich enough to verify the action-reconciliation logic that the service
runs after every share mutation.
"""

from datetime import datetime, timedelta

import pytest

from src.api.relationship import NoteRelationEnum, ObjectRef, Relationship, SubjectRef
from src.api.undefined import UNDEFINED
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.entities.user.user_action import FilterUserAction, UserActionEntity
from src.services.sharing import DefaultSharingService
from tests.stubs.logging import silent_logger
from tests.stubs.permission_repo import _FakePermissionRepo
from tests.stubs.permission_service import _FakePermissionService
from tests.stubs.sharing_repo import _FakeSharingRepo
from tests.stubs.user_action_repo import _FakeUserActionRepo
from tests.stubs.user_context import _UserContext
from tests.stubs.user_repo import _FakeUserRepo


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_service(
    repo: _FakeSharingRepo,
    permissions: _FakePermissionRepo,
    permission_service: _FakePermissionService | None = None,
    user_repo: _FakeUserRepo | None = None,
    user_action_repo: Optional[_FakeUserActionRepo] = None,
) -> DefaultSharingService:
    """Build a :class:`DefaultSharingService` with sensible test defaults.

    ``user_action_repo`` defaults to a fresh empty fake so the action
    tests are explicit about the dependency and other tests don't have
    to think about it.
    """
    return DefaultSharingService(
        sharing_repo=repo,
        user_repo=user_repo or _FakeUserRepo(),
        permission_repo=permissions,
        permission_service=permission_service or _FakePermissionService(),
        logging_provider=silent_logger,
        user_action_repo=user_action_repo or _FakeUserActionRepo(),
    )


def _share(
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
# Existing CRUD behaviour (kept from the pre-refactor suite)
# ---------------------------------------------------------------------------


async def test_create_share_sets_defaults_in_service() -> None:
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
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(repo, permissions)
    filter = FilterShareNote(note_id="note-1")

    share = await service.get_share(filter, _UserContext())

    assert share.id == "share-1"
    assert repo.get_shares_calls == [filter]


async def test_get_share_by_id_template_uses_get_shares_by_id() -> None:
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(repo, permissions)

    share = await service.get_share_by_id("share-1", _UserContext())

    assert share.id == "share-1"
    assert repo.get_shares_by_id_calls == [["share-1"]]


async def test_update_share_denies_without_edit_permission() -> None:
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-denied")])
    permissions = _FakePermissionRepo(editable_note_ids=set())
    permission_service = _FakePermissionService()
    service = _build_service(repo, permissions, permission_service)

    with pytest.raises(PermissionError):
        await service.update_share(NoteShareEntity(id="share-1", description="x"), _UserContext())

    assert permission_service.replace_calls == []


async def test_delete_shares_denies_without_edit_permission() -> None:
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-denied")])
    permissions = _FakePermissionRepo(editable_note_ids=set())
    service = _build_service(repo, permissions)

    with pytest.raises(PermissionError):
        await service.delete_shares(["share-1"], _UserContext())

    assert repo.deleted_ids is None


async def test_update_share_replaces_permission_via_permission_service() -> None:
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
    assert existing_reader not in rels
    assert Relationship(
        resource=ObjectRef("note", "note-1"),
        relation=NoteRelationEnum.WRITER,
        subject=SubjectRef("user", "access-user"),
    ) in rels


# ---------------------------------------------------------------------------
# user_action reconciliation
# ---------------------------------------------------------------------------


async def test_create_share_schedules_disable_action_when_online_until_set() -> None:
    """A share with a concrete ``online_until`` schedules a `disable` action."""
    expires_at = datetime(2026, 7, 1, 12, 0, 0)
    user_repo = _FakeUserRepo()
    action_repo = _FakeUserActionRepo()
    repo = _FakeSharingRepo()
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(
        repo,
        permissions,
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    created = await service.create_share(
        NoteShareEntity(
            note_id="note-1",
            online_until=expires_at,
            permission="read",
        ),
        _UserContext(),
    )

    assert len(user_repo.inserted) == 1
    access_as = user_repo.inserted[0].id
    assert created.access_as == access_as

    assert len(action_repo.add_action_calls) == 1
    action = action_repo.add_action_calls[0]
    assert action.user_id == access_as
    assert action.action == "disable"
    assert action.execute_at == expires_at

    # one disable row now stored, target user matches the access user
    stored = action_repo.for_user(access_as)
    assert len(stored) == 1
    assert stored[0].execute_at == expires_at


async def test_create_share_skips_action_when_online_until_none() -> None:
    """``online_until = None`` means the share never expires; no scheduling."""
    user_repo = _FakeUserRepo()
    action_repo = _FakeUserActionRepo()
    repo = _FakeSharingRepo()
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(
        repo,
        permissions,
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    await service.create_share(
        NoteShareEntity(note_id="note-1", online_until=None, permission="read"),
        _UserContext(),
    )

    assert action_repo.add_action_calls == []
    assert action_repo.all() == []


async def test_create_share_skips_action_when_online_until_undefined() -> None:
    """``UNDEFINED`` for ``online_until`` is treated as "indefinite"."""
    action_repo = _FakeUserActionRepo()
    service = _build_service(
        _FakeSharingRepo(),
        _FakePermissionRepo(editable_note_ids={"note-1"}),
        user_action_repo=action_repo,
    )

    await service.create_share(
        NoteShareEntity(note_id="note-1", permission="read"),
        _UserContext(),
    )

    assert action_repo.add_action_calls == []


async def test_create_share_requires_user_action_repo() -> None:
    """``user_action_repo`` is a required dependency; passing ``None`` raises."""
    service = DefaultSharingService(
        sharing_repo=_FakeSharingRepo(),
        user_repo=_FakeUserRepo(),
        permission_repo=_FakePermissionRepo(editable_note_ids={"note-1"}),
        permission_service=_FakePermissionService(),
        logging_provider=silent_logger,
        user_action_repo=None,  # type: ignore[arg-type]
    )

    with pytest.raises((TypeError, AttributeError)):
        await service.create_share(
            NoteShareEntity(
                note_id="note-1",
                online_until=datetime(2026, 7, 1),
                permission="read",
            ),
            _UserContext(),
        )


async def test_update_share_replaces_pending_disable_when_online_until_changes() -> None:
    """Setting a new ``online_until`` drops the old pending action and adds a fresh one."""
    expires_at = datetime(2026, 7, 1, 12, 0, 0)
    # pre-seed a pending disable that should be replaced
    pre_seeded = UserActionEntity(
        id="old-action",
        user_id="access-user",
        action="disable",
        execute_at=expires_at - timedelta(days=1),
    )
    action_repo = _FakeUserActionRepo(initial=[pre_seeded])
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1", access_as="access-user")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(repo, permissions, user_action_repo=action_repo)

    await service.update_share(
        NoteShareEntity(id="share-1", online_until=expires_at),
        _UserContext(),
    )

    # old action removed
    assert "old-action" in action_repo.remove_action_calls
    # new action added for the same user at the new timestamp
    assert len(action_repo.add_action_calls) == 1
    new_action = action_repo.add_action_calls[0]
    assert new_action.user_id == "access-user"
    assert new_action.action == "disable"
    assert new_action.execute_at == expires_at
    # the store now holds only the new action
    assert action_repo.for_user("access-user") == [new_action]


async def test_update_share_clears_pending_disable_when_online_until_set_to_none() -> None:
    """Setting ``online_until = None`` removes any pending disable rows."""
    pre_seeded = UserActionEntity(
        id="pending-disable",
        user_id="access-user",
        action="disable",
        execute_at=datetime(2026, 7, 1),
    )
    action_repo = _FakeUserActionRepo(initial=[pre_seeded])
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1", access_as="access-user")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(repo, permissions, user_action_repo=action_repo)

    await service.update_share(
        NoteShareEntity(id="share-1", online_until=None),
        _UserContext(),
    )

    assert action_repo.remove_action_calls == ["pending-disable"]
    assert action_repo.add_action_calls == []
    assert action_repo.for_user("access-user") == []


async def test_update_share_does_not_touch_actions_when_online_until_undefined() -> None:
    """Updates that don't touch ``online_until`` must not touch scheduling."""
    pre_seeded = UserActionEntity(
        id="do-not-touch",
        user_id="access-user",
        action="disable",
        execute_at=datetime(2026, 7, 1),
    )
    action_repo = _FakeUserActionRepo(initial=[pre_seeded])
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1", access_as="access-user")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(repo, permissions, user_action_repo=action_repo)

    await service.update_share(
        NoteShareEntity(id="share-1", description="new description"),
        _UserContext(),
    )

    assert action_repo.remove_action_calls == []
    assert action_repo.add_action_calls == []
    assert action_repo.get_actions_calls == []
    # original action untouched
    assert action_repo.for_user("access-user")[0].id == "do-not-touch"


async def test_delete_share_purges_pending_actions_for_access_user() -> None:
    """Deleting a share removes every user_action row targeting its access user."""
    pending = UserActionEntity(
        id="pending-1",
        user_id="access-user",
        action="disable",
        execute_at=datetime(2026, 7, 1),
    )
    already_done = UserActionEntity(
        id="done-1",
        user_id="access-user",
        action="delete",
        execute_at=datetime(2026, 6, 1),
        executed_at=datetime(2026, 6, 1),
    )
    # unrelated action for a different user must stay put
    other_user = UserActionEntity(
        id="other-user-1",
        user_id="somebody-else",
        action="disable",
        execute_at=datetime(2026, 7, 1),
    )
    action_repo = _FakeUserActionRepo(initial=[pending, already_done, other_user])
    user_repo = _FakeUserRepo()
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1", access_as="access-user")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(
        repo,
        permissions,
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    await service.delete_shares(["share-1"], _UserContext())

    # both rows for the access user are gone
    assert sorted(action_repo.remove_action_calls) == ["done-1", "pending-1"]
    # the unrelated user_action survives
    assert action_repo.for_user("somebody-else") == [other_user]
    # the get_actions_by_user lookup is exercised
    assert "access-user" in action_repo.get_actions_by_user_calls
    # the access user itself is deleted as before
    assert user_repo.deleted == ["access-user"]
    # and the share row is deleted
    assert repo.deleted_ids == ["share-1"]


async def test_delete_share_purges_actions_even_when_already_executed() -> None:
    """Even already-executed actions are removed when the access user is deleted.

    This avoids dangling references in the user_action table when the
    scheduler drops rows whose target user has been removed.
    """
    executed = UserActionEntity(
        id="executed-only",
        user_id="access-user",
        action="disable",
        execute_at=datetime(2026, 6, 1),
        executed_at=datetime(2026, 6, 1),
    )
    action_repo = _FakeUserActionRepo(initial=[executed])
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1", access_as="access-user")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(
        repo,
        permissions,
        user_action_repo=action_repo,
    )

    await service.delete_shares(["share-1"], _UserContext())

    assert action_repo.remove_action_calls == ["executed-only"]
    assert action_repo.for_user("access-user") == []


async def test_update_share_uses_get_actions_filter_to_find_pending_disable() -> None:
    """The reconciler must query the repo with both ``user_id`` and ``action='disable'``."""
    expires_at = datetime(2026, 7, 1)
    action_repo = _FakeUserActionRepo()
    repo = _FakeSharingRepo([_share(id="share-1", note_id="note-1", access_as="access-user")])
    permissions = _FakePermissionRepo(editable_note_ids={"note-1"})
    service = _build_service(repo, permissions, user_action_repo=action_repo)

    await service.update_share(
        NoteShareEntity(id="share-1", online_until=expires_at),
        _UserContext(),
    )

    assert len(action_repo.get_actions_calls) == 1
    filter_used: FilterUserAction = action_repo.get_actions_calls[0]
    assert filter_used.user_id == "access-user"
    assert filter_used.action == "disable"
    # pending rows only (None => IS NULL)
    assert filter_used.executed_at is None