"""Unit tests for :class:`ShareAccessService`.

The service is responsible for the *anonymous* access path: a public
client holds a share id from a URL and uses these methods to resolve
the share and its temporary access user.  Two methods are exercised
here:

* :meth:`ShareAccessService.access_share` returns the full share
  entity once it has verified the access user can still reach the note.
* :meth:`ShareAccessService.get_share_user` returns
  ``(access_as, online_until)`` for the share.

Both methods must reject shares whose access user has been disabled,
because a disabled user would either be unable to authenticate or be
denied downstream by the permission service.
"""

from datetime import datetime
from typing import List, Optional

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from src.api.relationship import ObjectRef, Relationship, SubjectRef
from src.db.entities.note.sharing import NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.db.entities.user.user_action import UserActionEntity
from src.services.share_access import ShareAccessService
from src.db.repos.user import RepoContextFactory, UnimplementedUserContext
from tests.stubs.logging import silent_logger
from tests.stubs.permission_repo import _FakePermissionRepo
from tests.stubs.sharing_repo import _FakeSharingRepo
from tests.stubs.user_action_repo import _FakeUserActionRepo
from tests.stubs.user_repo import _FakeUserRepo


# ---------------------------------------------------------------------------
# Local helpers
#
# All repos are pulled in from ``tests.stubs.*``.  We just need a small
# convenience builder that injects everything ``ShareAccessService``
# expects with sensible defaults.
# ---------------------------------------------------------------------------


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


def _build_service(
    *,
    sharing_repo: _FakeSharingRepo,
    user_repo: _FakeUserRepo,
    user_action_repo: _FakeUserActionRepo,
    permissions: Optional[_FakePermissionRepo] = None,
) -> ShareAccessService:
    return ShareAccessService(
        sharing_repo=sharing_repo,
        permission_repo=permissions or _FakePermissionRepo(
            editable_note_ids=set(),
            permissions_by_access_user={("note-1", "access-user"): ["reader"]},
        ),
        user_repo=user_repo,
        user_action_repo=user_action_repo,
        logger=silent_logger,
        context_factory=RepoContextFactory(user_repo),
    )


# ---------------------------------------------------------------------------
# access_share
# ---------------------------------------------------------------------------


async def test_access_share_returns_share_with_permission() -> None:
    """A share whose access user has read access is returned with ``permission='read'``."""
    share = _share()
    user_repo = _FakeUserRepo([UserEntity(id="access-user", username="x", type="temporary")])
    action_repo = _FakeUserActionRepo()
    service = _build_service(
        sharing_repo=_FakeSharingRepo([share]),
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    resolved = await service.access_share("share-1", UnimplementedUserContext())

    assert resolved.id == "share-1"
    assert resolved.permission == "read"


async def test_access_share_rejects_disabled_user() -> None:
    """``access_share`` raises PermissionError when the access user is disabled."""
    share = _share()
    user_repo = _FakeUserRepo([UserEntity(id="access-user", username="x", type="temporary")])
    disabled_at = datetime(2026, 6, 1, 12, 0, 0)
    action_repo = _FakeUserActionRepo(
        initial=[
            UserActionEntity(
                id="disable-1",
                user_id="access-user",
                action="disable",
                execute_at=disabled_at,
                executed_at=disabled_at,
            ),
        ]
    )
    service = _build_service(
        sharing_repo=_FakeSharingRepo([share]),
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    with pytest.raises(PermissionError):
        await service.access_share("share-1", UnimplementedUserContext())


# ---------------------------------------------------------------------------
# get_share_user
# ---------------------------------------------------------------------------


async def test_get_share_user_returns_access_as_and_online_until() -> None:
    """The happy path: returns the access user id and the share's expiry."""
    expires_at = datetime(2026, 7, 1)
    share = NoteShareEntity(
        id="share-1",
        note_id="note-1",
        created_at=datetime(2026, 1, 1),
        created_by="creator-1",
        access_as="access-user",
        online_until=expires_at,
    )
    user_repo = _FakeUserRepo([UserEntity(id="access-user", username="x", type="temporary")])
    service = _build_service(
        sharing_repo=_FakeSharingRepo([share]),
        user_repo=user_repo,
        user_action_repo=_FakeUserActionRepo(),
    )

    access_as, online_until = await service.get_share_user("share-1")

    assert access_as == "access-user"
    assert online_until == expires_at


async def test_get_share_user_returns_none_when_share_never_expires() -> None:
    """``online_until=None`` is propagated as None, not UNDEFINED."""
    share = NoteShareEntity(
        id="share-1",
        note_id="note-1",
        created_at=datetime(2026, 1, 1),
        created_by="creator-1",
        access_as="access-user",
        online_until=None,
    )
    user_repo = _FakeUserRepo([UserEntity(id="access-user", username="x", type="temporary")])
    service = _build_service(
        sharing_repo=_FakeSharingRepo([share]),
        user_repo=user_repo,
        user_action_repo=_FakeUserActionRepo(),
    )

    access_as, online_until = await service.get_share_user("share-1")

    assert access_as == "access-user"
    assert online_until is None


async def test_get_share_user_raises_when_share_not_found() -> None:
    service = _build_service(
        sharing_repo=_FakeSharingRepo([]),
        user_repo=_FakeUserRepo(),
        user_action_repo=_FakeUserActionRepo(),
    )

    with pytest.raises(ValueError, match="Share not found"):
        await service.get_share_user("missing")


async def test_get_share_user_raises_when_access_user_missing() -> None:
    """A dangling ``access_as`` (user removed) must surface as ValueError."""
    share = _share(access_as="ghost")
    service = _build_service(
        sharing_repo=_FakeSharingRepo([share]),
        user_repo=_FakeUserRepo([]),  # no users
        user_action_repo=_FakeUserActionRepo(),
    )

    with pytest.raises(ValueError, match="no longer exists"):
        await service.get_share_user("share-1")


async def test_get_share_user_raises_when_access_user_disabled() -> None:
    """A disabled access user must not be handed out to anonymous callers."""
    share = _share(access_as="access-user")
    user_repo = _FakeUserRepo([UserEntity(id="access-user", username="x", type="temporary")])
    disabled_at = datetime(2026, 6, 1)
    action_repo = _FakeUserActionRepo(
        initial=[
            UserActionEntity(
                id="disable-1",
                user_id="access-user",
                action="disable",
                execute_at=disabled_at,
                executed_at=disabled_at,
            ),
        ]
    )
    service = _build_service(
        sharing_repo=_FakeSharingRepo([share]),
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    with pytest.raises(PermissionError, match="disabled"):
        await service.get_share_user("share-1")


async def test_get_share_user_treats_reenabled_user_as_enabled() -> None:
    """The most recent executed action wins; an ``enable`` after ``disable`` clears the flag."""
    share = _share(access_as="access-user")
    user_repo = _FakeUserRepo([UserEntity(id="access-user", username="x", type="temporary")])
    action_repo = _FakeUserActionRepo(
        initial=[
            UserActionEntity(
                id="disable-old",
                user_id="access-user",
                action="disable",
                execute_at=datetime(2026, 5, 1),
                executed_at=datetime(2026, 5, 1),
            ),
            UserActionEntity(
                id="enable-newer",
                user_id="access-user",
                action="enable",
                execute_at=datetime(2026, 6, 1),
                executed_at=datetime(2026, 6, 1),
            ),
        ]
    )
    service = _build_service(
        sharing_repo=_FakeSharingRepo([share]),
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    # Should not raise because the latest action is `enable`.
    access_as, _ = await service.get_share_user("share-1")
    assert access_as == "access-user"


async def test_get_share_user_ignores_pending_disable() -> None:
    """A *pending* disable (not yet executed) does NOT mark the user disabled."""
    share = _share(access_as="access-user")
    user_repo = _FakeUserRepo([UserEntity(id="access-user", username="x", type="temporary")])
    action_repo = _FakeUserActionRepo(
        initial=[
            UserActionEntity(
                id="pending-disable",
                user_id="access-user",
                action="disable",
                execute_at=datetime(2026, 7, 1),
                # no executed_at -> still pending
            ),
        ]
    )
    service = _build_service(
        sharing_repo=_FakeSharingRepo([share]),
        user_repo=user_repo,
        user_action_repo=action_repo,
    )

    # pending actions don't disable yet
    access_as, _ = await service.get_share_user("share-1")
    assert access_as == "access-user"