"""Tests for the ``SearchUser`` RPC of :class:`GrpcUserService`.

Wires the **real** adapter + visitor to a stub :class:`UserServiceABC`
so the proto -> adapter -> visitor path is exercised without Postgres.
"""

from __future__ import annotations

from typing import List, Optional, cast

from grpc.aio import ServicerContext

from src.api.services.user_service import USER_SEARCH_LIMIT
from src.db.entities.user.user import UserEntity
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.user_pb2 import GetUsersRequest, SearchUserRequest
from src.grpc_mod.user_service import GrpcUserService
from tests.doubles import _StubUserContextFactory, _StubUserService, silent_logger
from tests.doubles.dummies.fake_context import _DummyContext


def _service(
    results: Optional[List[UserEntity]] = None,
) -> tuple[GrpcUserService, _StubUserService]:
    stub = _StubUserService(results)
    adapter = GrpcUserService(
        user_service=stub,
        log=silent_logger,
        to_grpc=ConvertToGrpcVisitor(),
        context_factory=_StubUserContextFactory(),
    )
    return adapter, stub


def _ctx() -> _DummyContext:
    return _DummyContext()


async def test_search_user_returns_converted_users() -> None:
    """Matched entities come back as proto ``User`` messages."""
    users = [
        UserEntity(
            id="u-1",
            discord_id=1,
            avatar="avatar.png",
            username="paul",
            email="paul@example.com",
        ),
        UserEntity(
            id="u-2",
            discord_id=2,
            username="paula",
            email="paula@example.com",
        ),
    ]
    adapter, _ = _service(users)

    reply = await adapter.SearchUser(
        SearchUserRequest(query="pau", user_id="caller-1"),
        cast(ServicerContext, _ctx()),
    )

    assert [user.id for user in reply.users] == ["u-1", "u-2"]
    assert [user.username for user in reply.users] == ["paul", "paula"]
    assert [user.email for user in reply.users] == [
        "paul@example.com",
        "paula@example.com",
    ]


async def test_search_user_forwards_query_and_user_context() -> None:
    """The request query and user id reach the service; limit is the default."""
    adapter, stub = _service([])

    await adapter.SearchUser(
        SearchUserRequest(query="paul@example.com", user_id="caller-1"),
        cast(ServicerContext, _ctx()),
    )

    assert stub.calls == [("paul@example.com", "caller-1", USER_SEARCH_LIMIT)]


async def test_search_user_converts_users_with_missing_optional_fields() -> None:
    """Users without an email/avatar/discord id still convert cleanly."""
    adapter, _ = _service(
        [UserEntity(id="u-3", username="ghost", email=None, avatar=None)]
    )

    reply = await adapter.SearchUser(
        SearchUserRequest(query="ghost", user_id="caller-1"),
        cast(ServicerContext, _ctx()),
    )

    assert len(reply.users) == 1
    user = reply.users[0]
    assert user.id == "u-3"
    assert user.username == "ghost"
    assert user.email == ""
    assert user.avatar == ""
    assert user.discord_id == 0


async def test_search_user_returns_empty_reply_for_no_matches() -> None:
    """No matches yields an empty ``SearchUserResponse``."""
    adapter, _ = _service([])

    reply = await adapter.SearchUser(
        SearchUserRequest(query="nobody", user_id="caller-1"),
        cast(ServicerContext, _ctx()),
    )

    assert list(reply.users) == []

# ---------------------------------------------------------------------------
# GetUsers (batch read)
# ---------------------------------------------------------------------------


async def test_get_users_returns_converted_users() -> None:
    """The batch read comes back as proto ``User`` messages."""
    users = [
        UserEntity(
            id="u-1",
            discord_id=1,
            avatar="avatar.png",
            username="paul",
            email="paul@example.com",
        ),
        UserEntity(id="u-2", discord_id=2, username="paula"),
    ]
    adapter, _ = _service(users)

    reply = await adapter.GetUsers(
        GetUsersRequest(user_id="caller-1", ids=["u-1", "u-2"]),
        cast(ServicerContext, _ctx()),
    )

    assert [user.id for user in reply.users] == ["u-1", "u-2"]
    assert [user.username for user in reply.users] == ["paul", "paula"]


async def test_get_users_forwards_ids_and_user_context() -> None:
    """The requested ids and the caller id reach the service layer."""
    adapter, stub = _service([])

    await adapter.GetUsers(
        GetUsersRequest(user_id="caller-1", ids=["u-1", "u-2"]),
        cast(ServicerContext, _ctx()),
    )

    assert stub.batch_calls == [(["u-1", "u-2"], "caller-1")]


async def test_get_users_empty_ids_returns_empty_reply() -> None:
    """No ids in the request means an empty reply and empty service input."""
    adapter, stub = _service([])

    reply = await adapter.GetUsers(
        GetUsersRequest(user_id="caller-1"),
        cast(ServicerContext, _ctx()),
    )

    assert list(reply.users) == []
    assert stub.batch_calls == [([], "caller-1")]
