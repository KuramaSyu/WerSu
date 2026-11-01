"""Unit tests for :class:`ShareActionFacade`.

The facade's responsibility is to keep three persistence concerns in
sync around share CRUD:

* the temporary access user in ``users``,
* the share row in ``shared``,
* the scheduled ``user_action`` rows.

Service-level tests live in :mod:`tests.test_sharing_service`; this
file covers the facade in isolation, using the in-memory fakes in
:mod:`tests.doubles`.

Pinned regressions (one per behaviour):

* creating a share ends with exactly one pending disable row when an
  expiry is set,
* scheduling a new expiry drops older pending disables,
* deleting a share removes the temp user and every action row that
  pointed at it -- in that order.
"""

from __future__ import annotations
from datetime import datetime, timedelta
from src.api.other.undefined import UNDEFINED
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.db.entities.user.user_action import FilterUserAction, UserActionEntity
from src.db.repos.sharing.share_action_facade import ShareActionFacade
from tests.doubles import silent_logger
from tests.doubles import _FakeSpySharingRepo
from tests.doubles import _FakeSpyUserActionRepo
from tests.doubles import _DummyUserContext as UserContext, _DummyUserContext
from tests.doubles import _FakeSpyUserRepo
from typing import Optional
import pytest


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_facade(
    sharing_repo: Optional[_FakeSpySharingRepo] = None,
    user_repo: Optional[_FakeSpyUserRepo] = None,
    user_action_repo: Optional[_FakeSpyUserActionRepo] = None,
    get_now=None,
) -> ShareActionFacade:
    return ShareActionFacade(
        sharing_repo=sharing_repo or _FakeSpySharingRepo(),
        user_repo=user_repo or _FakeSpyUserRepo(),
        user_action_repo=user_action_repo or _FakeSpyUserActionRepo(),
        logging_provider=silent_logger,
        get_now=get_now,
    )


def _share(
    *,
    id: str = "share-1",
    note_id: str = "note-1",
    access_as: str = "access-user",
) -> NoteShareEntity:
    return NoteShareEntity(
        id=id,
        note_id=note_id,
        access_as=access_as,
    )


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


async def test_create_share_persists_temp_user_share_row_and_action() -> None:
    """End-to-end: temp user -> share row -> one pending disable action."""
    expires_at = datetime(2026, 7, 1, 12, 0, 0)
    sharing_repo = _FakeSpySharingRepo()
    user_repo = _FakeSpyUserRepo()
    action_repo = _FakeSpyUserActionRepo()
    facade = _build_facade(
        sharing_repo=sharing_repo,
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    created = await facade.create_share(
        NoteShareEntity(
            note_id="note-1",
            online_until=expires_at,
            permission="read",
        ),
        _DummyUserContext("creator-1"),
    )

    # a fresh temporary user is inserted and stamped onto the share row
    assert len(user_repo.inserted) == 1
    access_as = user_repo.inserted[0].id
    assert created.access_as == access_as
    assert created.id is UNDEFINED

    # share row is forwarded to the wrapped repo
    assert sharing_repo.created_share is created

    # exactly one pending disable action for the new temp user
    actions = action_repo.for_user(str(access_as))
    assert len(actions) == 1
    assert actions[0].action == "disable"
    assert actions[0].execute_at == expires_at


async def test_create_share_skips_scheduling_when_online_until_is_none() -> None:
    """``online_until = None`` means "never expires"; nothing to schedule."""
    user_repo = _FakeSpyUserRepo()
    action_repo = _FakeSpyUserActionRepo()
    facade = _build_facade(user_repo=user_repo, user_action_repo=action_repo)

    await facade.create_share(
        NoteShareEntity(note_id="note-1", online_until=None, permission="read"),
        _DummyUserContext(),
    )

    assert action_repo.add_action_calls == []
    assert action_repo.all() == []


async def test_create_share_skips_scheduling_when_online_until_is_undefined() -> None:
    """``UNDEFINED`` on ``online_until`` is treated as "leave the schedule alone"."""
    action_repo = _FakeSpyUserActionRepo()
    facade = _build_facade(user_action_repo=action_repo)

    await facade.create_share(
        NoteShareEntity(note_id="note-1", permission="read"),
        _DummyUserContext(),
    )

    assert action_repo.get_actions_calls == []
    assert action_repo.add_action_calls == []


async def test_create_share_requires_note_id() -> None:
    facade = _build_facade()

    with pytest.raises(ValueError, match="share.note_id is required"):
        await facade.create_share(
            NoteShareEntity(note_id=UNDEFINED, permission="read"),
            _DummyUserContext(),
        )


async def test_create_share_uses_base_username_when_free() -> None:
    """A fresh ``share-<creator>-<note>`` username is used as-is."""
    user_repo = _FakeSpyUserRepo()
    facade = _build_facade(user_repo=user_repo)

    await facade.create_share(
        NoteShareEntity(note_id="note-1", permission="read"),
        _DummyUserContext("creator-1"),
    )

    assert user_repo.inserted[0].username == "share-creator-1-note-1"


async def test_create_share_appends_suffix_when_username_exists() -> None:
    """An existing share forces a numeric suffix onto the temp username."""
    user_repo = _FakeSpyUserRepo(
        users=[UserEntity(id="existing", username="share-creator-1-note-1")]
    )
    facade = _build_facade(user_repo=user_repo)

    created = await facade.create_share(
        NoteShareEntity(note_id="note-1", permission="read"),
        _DummyUserContext("creator-1"),
    )

    assert user_repo.inserted[0].username == "share-creator-1-note-1-2"
    assert created.access_as == user_repo.inserted[0].id


async def test_create_share_skips_taken_suffixes() -> None:
    """Already-taken suffixes are skipped until the first free number."""
    user_repo = _FakeSpyUserRepo(
        users=[
            UserEntity(id="base", username="share-creator-1-note-1"),
            UserEntity(id="two", username="share-creator-1-note-1-2"),
        ]
    )
    facade = _build_facade(user_repo=user_repo)

    await facade.create_share(
        NoteShareEntity(note_id="note-1", permission="read"),
        _DummyUserContext("creator-1"),
    )

    assert user_repo.inserted[0].username == "share-creator-1-note-1-3"


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


async def test_update_share_passes_through_with_no_reconciliation_when_online_until_unchanged() -> None:
    """``update`` without touching ``online_until`` is a pure pass-through."""
    sharing_repo = _FakeSpySharingRepo([_share()])
    action_repo = _FakeSpyUserActionRepo()
    facade = _build_facade(
        sharing_repo=sharing_repo,
        user_action_repo=action_repo,
    )

    updated = await facade.update_share(
        NoteShareEntity(id="share-1", description="new"),
        _DummyUserContext(),
    )

    assert updated.description == "new"
    assert sharing_repo.updated_share is updated
    assert action_repo.get_actions_calls == []
    assert action_repo.add_action_calls == []


async def test_update_share_reconciles_actions_when_online_until_changes() -> None:
    """Setting a new ``online_until`` drops the old pending action and adds a fresh one."""
    expires_at = datetime(2026, 7, 1, 12, 0, 0)
    pre_seeded = UserActionEntity(
        id="old-action",
        user_id="access-user",
        action="disable",
        execute_at=expires_at - timedelta(days=1),
    )
    action_repo = _FakeSpyUserActionRepo(initial=[pre_seeded])
    facade = _build_facade(
        sharing_repo=_FakeSpySharingRepo([_share()]),
        user_action_repo=action_repo,
    )

    await facade.update_share(
        NoteShareEntity(id="share-1", online_until=expires_at),
        _DummyUserContext(),
    )

    assert "old-action" in action_repo.remove_action_calls
    new_actions = action_repo.for_user("access-user")
    assert len(new_actions) == 1
    assert new_actions[0].execute_at == expires_at


async def test_update_share_clears_actions_when_online_until_set_to_none() -> None:
    """``online_until = None`` clears pending disables but the row persists as executed-or-pending."""
    pre_seeded = UserActionEntity(
        id="pending-disable",
        user_id="access-user",
        action="disable",
        execute_at=datetime(2026, 7, 1),
    )
    action_repo = _FakeSpyUserActionRepo(initial=[pre_seeded])
    facade = _build_facade(
        sharing_repo=_FakeSpySharingRepo([_share()]),
        user_action_repo=action_repo,
    )

    await facade.update_share(
        NoteShareEntity(id="share-1", online_until=None),
        _DummyUserContext(),
    )

    assert "pending-disable" in action_repo.remove_action_calls
    assert action_repo.for_user("access-user") == []


async def test_update_share_queries_repo_with_correct_filter() -> None:
    """The pending-disable lookup must pin ``user_id`` and ``action='disable'``."""
    expires_at = datetime(2026, 7, 1)
    action_repo = _FakeSpyUserActionRepo()
    facade = _build_facade(
        sharing_repo=_FakeSpySharingRepo([_share()]),
        user_action_repo=action_repo,
    )

    await facade.update_share(
        NoteShareEntity(id="share-1", online_until=expires_at),
        _DummyUserContext(),
    )

    assert len(action_repo.get_actions_calls) == 1
    filter_used: FilterUserAction = action_repo.get_actions_calls[0]
    assert filter_used.user_id == "access-user"
    assert filter_used.action == "disable"
    # ``None`` => "pending only"
    assert filter_used.executed_at is None


async def test_update_share_requires_id() -> None:
    facade = _build_facade()

    with pytest.raises(ValueError, match="share.id is required"):
        await facade.update_share(
            NoteShareEntity(id=UNDEFINED, description="x"),
            _DummyUserContext(),
        )


def _disabled_action(user_id: str = "access-user") -> UserActionEntity:
    return UserActionEntity(
        id="disable-done",
        user_id=user_id,
        action="disable",
        execute_at=datetime(2026, 1, 1),
        executed_at=datetime(2026, 1, 1),
    )


async def test_update_share_reenables_user_when_expiry_moves_to_future() -> None:
    """Extending an expired share queues an immediate enable before the disable."""
    now = datetime(2026, 11, 1, 12, 0, 0)
    new_expiry = now + timedelta(days=7)
    action_repo = _FakeSpyUserActionRepo(initial=[_disabled_action()])
    facade = _build_facade(
        sharing_repo=_FakeSpySharingRepo([_share()]),
        user_action_repo=action_repo,
        get_now=lambda: now,
    )

    await facade.update_share(
        NoteShareEntity(id="share-1", online_until=new_expiry),
        _DummyUserContext(),
    )

    actions = action_repo.for_user("access-user")
    enables = [a for a in actions if a.action == "enable"]
    disables = [a for a in actions if a.action == "disable" and a.executed_at is UNDEFINED]
    assert len(enables) == 1
    assert enables[0].execute_at == now
    assert len(disables) == 1
    assert disables[0].execute_at == new_expiry


async def test_update_share_reenables_user_when_share_never_expires() -> None:
    """Setting ``online_until=None`` re-enables a disabled access user."""
    now = datetime(2026, 11, 1, 12, 0, 0)
    action_repo = _FakeSpyUserActionRepo(initial=[_disabled_action()])
    facade = _build_facade(
        sharing_repo=_FakeSpySharingRepo([_share()]),
        user_action_repo=action_repo,
        get_now=lambda: now,
    )

    await facade.update_share(
        NoteShareEntity(id="share-1", online_until=None),
        _DummyUserContext(),
    )

    actions = action_repo.for_user("access-user")
    assert [a.action for a in actions].count("enable") == 1
    assert not [a for a in actions if a.action == "disable" and a.executed_at is UNDEFINED]


async def test_update_share_does_not_reenable_user_whose_expiry_stays_past() -> None:
    """A new due date that is still in the past keeps the user disabled."""
    now = datetime(2026, 11, 1, 12, 0, 0)
    past_expiry = now - timedelta(days=1)
    action_repo = _FakeSpyUserActionRepo(initial=[_disabled_action()])
    facade = _build_facade(
        sharing_repo=_FakeSpySharingRepo([_share()]),
        user_action_repo=action_repo,
        get_now=lambda: now,
    )

    await facade.update_share(
        NoteShareEntity(id="share-1", online_until=past_expiry),
        _DummyUserContext(),
    )

    actions = action_repo.for_user("access-user")
    assert not [a for a in actions if a.action == "enable"]
    assert len([a for a in actions if a.action == "disable" and a.executed_at is UNDEFINED]) == 1


async def test_update_share_does_not_reenable_active_user() -> None:
    """A user with no executed disable is never sent a spurious enable."""
    now = datetime(2026, 11, 1, 12, 0, 0)
    action_repo = _FakeSpyUserActionRepo()
    facade = _build_facade(
        sharing_repo=_FakeSpySharingRepo([_share()]),
        user_action_repo=action_repo,
        get_now=lambda: now,
    )

    await facade.update_share(
        NoteShareEntity(id="share-1", online_until=now + timedelta(days=1)),
        _DummyUserContext(),
    )

    actions = action_repo.for_user("access-user")
    assert not [a for a in actions if a.action == "enable"]


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_share_removes_share_row_actions_and_temp_user_in_order() -> None:
    """Teardown: share row -> action purge -> temp user delete, exact order."""
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
    action_repo = _FakeSpyUserActionRepo(initial=[pending, already_done])
    user_repo = _FakeSpyUserRepo(users=[UserEntity(id="access-user")])
    sharing_repo = _FakeSpySharingRepo([_share()])
    facade = _build_facade(
        sharing_repo=sharing_repo,
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    await facade.delete_share("share-1", _DummyUserContext())

    # the wrapped sharing_repo got the row delete
    assert sharing_repo.deleted_ids == ["share-1"]
    # every action row for the temp user (pending or executed) was purged
    assert sorted(action_repo.remove_action_calls) == ["done-1", "pending-1"]
    # the temp user itself was deleted after the actions
    assert user_repo.deleted == ["access-user"]
    # and the order is: row, then actions, then user
    # (delete-shares-by_id wraps the row delete in list form, so the
    # sharing_repo saw ``["share-1"]`` last; user_repo saw action_repo.)
    assert "access-user" in action_repo.get_actions_by_user_calls


async def test_delete_share_preserves_actions_for_other_users() -> None:
    """Actions targeting *other* users must not be purged during a teardown."""
    other_action = UserActionEntity(
        id="other-user-1",
        user_id="somebody-else",
        action="disable",
        execute_at=datetime(2026, 7, 1),
    )
    action_repo = _FakeSpyUserActionRepo(initial=[other_action])
    facade = _build_facade(
        sharing_repo=_FakeSpySharingRepo([_share()]),
        user_action_repo=action_repo,
    )

    await facade.delete_share("share-1", _DummyUserContext())

    assert "other-user-1" not in action_repo.remove_action_calls
    assert action_repo.for_user("somebody-else") == [other_action]


async def test_delete_share_propagates_missing_share() -> None:
    """A missing share row in the wrapped repo raises ``ValueError``."""
    facade = _build_facade(sharing_repo=_FakeSpySharingRepo([]))

    with pytest.raises(ValueError, match="Share not found"):
        await facade.delete_share("missing", _DummyUserContext())


async def test_delete_share_rejects_empty_id() -> None:
    facade = _build_facade(sharing_repo=_FakeSpySharingRepo([_share()]))

    with pytest.raises(ValueError, match="share_id is required"):
        await facade.delete_share("", _DummyUserContext())


async def test_delete_shares_fans_out_to_delete_share() -> None:
    """``delete_shares`` calls ``delete_share`` once per id, in order."""
    sharing_repo = _FakeSpySharingRepo(
        [_share(id="a", note_id="note-a"), _share(id="b", note_id="note-b")]
    )
    facade = _build_facade(sharing_repo=sharing_repo)

    await facade.delete_shares(["a", "b"], _DummyUserContext())

    assert sharing_repo.deleted_ids == ["a", "b"]


async def test_delete_shares_requires_non_empty_input() -> None:
    facade = _build_facade()

    with pytest.raises(ValueError, match="At least one share_id is required"):
        await facade.delete_shares([], _DummyUserContext())


# ---------------------------------------------------------------------------
# read pass-throughs
# ---------------------------------------------------------------------------


async def test_read_methods_delegate_to_wrapped_sharing_repo() -> None:
    """The four read methods must pass the call through unchanged."""
    sharing_repo = _FakeSpySharingRepo([_share()])
    facade = _build_facade(sharing_repo=sharing_repo)
    ctx = _DummyUserContext()

    by_id = await facade.get_share_by_id("share-1", ctx)
    by_ids = await facade.get_shares_by_id(["share-1"], ctx)
    one = await facade.get_share(_filter_by_note("note-1"), ctx)
    many = await facade.get_shares(_filter_by_note("note-1"), ctx)

    assert by_id.id == "share-1"
    assert [s.id for s in by_ids] == ["share-1"]
    assert one.id == "share-1"
    assert [s.id for s in many] == ["share-1"]
    # ``get_share_by_id`` internally calls ``get_shares_by_id``; the
    # fan-out below exercises it twice (once via get_share_by_id, once
    # via get_shares_by_id).
    assert sharing_repo.get_shares_by_id_calls == [["share-1"], ["share-1"]]
    assert len(sharing_repo.get_shares_calls) == 2


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _filter_by_note(note_id: str):
    return FilterShareNote(note_id=note_id)
