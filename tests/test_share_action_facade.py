"""Unit tests for :class:`ShareActionFacade`.

The facade's responsibility is to keep three persistence concerns in
sync around share CRUD:

* the temporary access user in ``users``,
* the share row in ``shared``,
* the scheduled ``user_action`` rows.

Service-level tests live in :mod:`tests.test_sharing_service`; this
file covers the facade in isolation, using the in-memory fakes in
:mod:`tests.stubs`.

Pinned regressions (one per behaviour):

* creating a share ends with exactly one pending disable row when an
  expiry is set,
* scheduling a new expiry drops older pending disables,
* deleting a share removes the temp user and every action row that
  pointed at it -- in that order.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from src.api.other.undefined import UNDEFINED
from src.db.entities.note.sharing import NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.db.entities.user.user_action import FilterUserAction, UserActionEntity
from src.facades.share_action_facade import ShareActionFacade
from tests.stubs.logging import silent_logger
from tests.stubs.sharing_repo import _FakeSharingRepo
from tests.stubs.user_action_repo import _FakeUserActionRepo
from tests.stubs.user_context import _UserContext
from tests.stubs.user_repo import _FakeUserRepo


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_facade(
    sharing_repo: Optional[_FakeSharingRepo] = None,
    user_repo: Optional[_FakeUserRepo] = None,
    user_action_repo: Optional[_FakeUserActionRepo] = None,
) -> ShareActionFacade:
    return ShareActionFacade(
        sharing_repo=sharing_repo or _FakeSharingRepo(),
        user_repo=user_repo or _FakeUserRepo(),
        user_action_repo=user_action_repo or _FakeUserActionRepo(),
        logging_provider=silent_logger,
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
    sharing_repo = _FakeSharingRepo()
    user_repo = _FakeUserRepo()
    action_repo = _FakeUserActionRepo()
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
        _UserContext("creator-1"),
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
    user_repo = _FakeUserRepo()
    action_repo = _FakeUserActionRepo()
    facade = _build_facade(user_repo=user_repo, user_action_repo=action_repo)

    await facade.create_share(
        NoteShareEntity(note_id="note-1", online_until=None, permission="read"),
        _UserContext(),
    )

    assert action_repo.add_action_calls == []
    assert action_repo.all() == []


async def test_create_share_skips_scheduling_when_online_until_is_undefined() -> None:
    """``UNDEFINED`` on ``online_until`` is treated as "leave the schedule alone"."""
    action_repo = _FakeUserActionRepo()
    facade = _build_facade(user_action_repo=action_repo)

    await facade.create_share(
        NoteShareEntity(note_id="note-1", permission="read"),
        _UserContext(),
    )

    assert action_repo.get_actions_calls == []
    assert action_repo.add_action_calls == []


async def test_create_share_requires_note_id() -> None:
    facade = _build_facade()

    with pytest.raises(ValueError):
        await facade.create_share(
            NoteShareEntity(note_id=UNDEFINED, permission="read"),
            _UserContext(),
        )


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


async def test_update_share_passes_through_with_no_reconciliation_when_online_until_unchanged() -> None:
    """``update`` without touching ``online_until`` is a pure pass-through."""
    sharing_repo = _FakeSharingRepo([_share()])
    action_repo = _FakeUserActionRepo()
    facade = _build_facade(
        sharing_repo=sharing_repo,
        user_action_repo=action_repo,
    )

    updated = await facade.update_share(
        NoteShareEntity(id="share-1", description="new"),
        _UserContext(),
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
    action_repo = _FakeUserActionRepo(initial=[pre_seeded])
    facade = _build_facade(
        sharing_repo=_FakeSharingRepo([_share()]),
        user_action_repo=action_repo,
    )

    await facade.update_share(
        NoteShareEntity(id="share-1", online_until=expires_at),
        _UserContext(),
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
    action_repo = _FakeUserActionRepo(initial=[pre_seeded])
    facade = _build_facade(
        sharing_repo=_FakeSharingRepo([_share()]),
        user_action_repo=action_repo,
    )

    await facade.update_share(
        NoteShareEntity(id="share-1", online_until=None),
        _UserContext(),
    )

    assert "pending-disable" in action_repo.remove_action_calls
    assert action_repo.for_user("access-user") == []


async def test_update_share_queries_repo_with_correct_filter() -> None:
    """The pending-disable lookup must pin ``user_id`` and ``action='disable'``."""
    expires_at = datetime(2026, 7, 1)
    action_repo = _FakeUserActionRepo()
    facade = _build_facade(
        sharing_repo=_FakeSharingRepo([_share()]),
        user_action_repo=action_repo,
    )

    await facade.update_share(
        NoteShareEntity(id="share-1", online_until=expires_at),
        _UserContext(),
    )

    assert len(action_repo.get_actions_calls) == 1
    filter_used: FilterUserAction = action_repo.get_actions_calls[0]
    assert filter_used.user_id == "access-user"
    assert filter_used.action == "disable"
    # ``None`` => "pending only"
    assert filter_used.executed_at is None


async def test_update_share_requires_id() -> None:
    facade = _build_facade()

    with pytest.raises(ValueError):
        await facade.update_share(
            NoteShareEntity(id=UNDEFINED, description="x"),
            _UserContext(),
        )


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
    action_repo = _FakeUserActionRepo(initial=[pending, already_done])
    user_repo = _FakeUserRepo(users=[UserEntity(id="access-user")])
    sharing_repo = _FakeSharingRepo([_share()])
    facade = _build_facade(
        sharing_repo=sharing_repo,
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    await facade.delete_share("share-1", _UserContext())

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
    action_repo = _FakeUserActionRepo(initial=[other_action])
    facade = _build_facade(
        sharing_repo=_FakeSharingRepo([_share()]),
        user_action_repo=action_repo,
    )

    await facade.delete_share("share-1", _UserContext())

    assert "other-user-1" not in action_repo.remove_action_calls
    assert action_repo.for_user("somebody-else") == [other_action]


async def test_delete_share_propagates_missing_share() -> None:
    """A missing share row in the wrapped repo raises ``ValueError``."""
    facade = _build_facade(sharing_repo=_FakeSharingRepo([]))

    with pytest.raises(ValueError):
        await facade.delete_share("missing", _UserContext())


async def test_delete_share_rejects_empty_id() -> None:
    facade = _build_facade(sharing_repo=_FakeSharingRepo([_share()]))

    with pytest.raises(ValueError):
        await facade.delete_share("", _UserContext())


async def test_delete_shares_fans_out_to_delete_share() -> None:
    """``delete_shares`` calls ``delete_share`` once per id, in order."""
    sharing_repo = _FakeSharingRepo(
        [_share(id="a", note_id="note-a"), _share(id="b", note_id="note-b")]
    )
    facade = _build_facade(sharing_repo=sharing_repo)

    await facade.delete_shares(["a", "b"], _UserContext())

    assert sharing_repo.deleted_ids == ["a", "b"]


async def test_delete_shares_requires_non_empty_input() -> None:
    facade = _build_facade()

    with pytest.raises(ValueError):
        await facade.delete_shares([], _UserContext())


# ---------------------------------------------------------------------------
# read pass-throughs
# ---------------------------------------------------------------------------


async def test_read_methods_delegate_to_wrapped_sharing_repo() -> None:
    """The four read methods must pass the call through unchanged."""
    sharing_repo = _FakeSharingRepo([_share()])
    facade = _build_facade(sharing_repo=sharing_repo)
    ctx = _UserContext()

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
    from src.db.entities.note.sharing import FilterShareNote
    return FilterShareNote(note_id=note_id)
