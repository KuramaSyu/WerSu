"""Unit tests for the disabled-state contract on :class:`UserContextABC`.

``is_disabled`` lives on the context so call sites do not need to know
about the ``user_action`` table.  The repo-backed context derives it
from the newest *executed* action, while the anonymous / dummy contexts
report ``False``.
"""

from __future__ import annotations

from datetime import datetime

from src.db.entities.user.user_action import UserActionEntity
from src.db.repos.user.context import RepoContextFactory, RepoUserContext
from src.db.repos.user import UnimplementedUserContext
from tests.doubles import (
    _DummyUserActionRepo,
    _DummyUserContext,
    _FakeSpyUserActionRepo,
    _FakeSpyUserRepo,
)


def _executed(
    action_id: str, action: str, when: datetime
) -> UserActionEntity:
    return UserActionEntity(
        id=action_id,
        user_id="access-user",
        action=action,
        execute_at=when,
        executed_at=when,
    )


async def test_repo_context_reports_disabled_after_executed_disable() -> None:
    action_repo = _FakeSpyUserActionRepo(
        initial=[_executed("disable-1", "disable", datetime(2026, 6, 1))]
    )
    ctx = RepoUserContext(_FakeSpyUserRepo(), "access-user", action_repo)

    assert await ctx.is_disabled() is True


async def test_repo_context_reports_enabled_when_enable_is_newer() -> None:
    action_repo = _FakeSpyUserActionRepo(
        initial=[
            _executed("disable-1", "disable", datetime(2026, 6, 1)),
            _executed("enable-1", "enable", datetime(2026, 6, 2)),
        ]
    )
    ctx = RepoUserContext(_FakeSpyUserRepo(), "access-user", action_repo)

    assert await ctx.is_disabled() is False


async def test_repo_context_ignores_pending_disable() -> None:
    action_repo = _FakeSpyUserActionRepo(
        initial=[
            UserActionEntity(
                id="pending",
                user_id="access-user",
                action="disable",
                execute_at=datetime(2026, 6, 1),
            )
        ]
    )
    ctx = RepoUserContext(_FakeSpyUserRepo(), "access-user", action_repo)

    assert await ctx.is_disabled() is False


async def test_repo_context_without_action_repo_reports_enabled() -> None:
    ctx = RepoUserContext(_FakeSpyUserRepo(), "access-user")

    assert await ctx.is_disabled() is False


async def test_factory_wires_action_repo_into_context() -> None:
    action_repo = _FakeSpyUserActionRepo(
        initial=[_executed("disable-1", "disable", datetime(2026, 6, 1))]
    )
    factory = RepoContextFactory(_FakeSpyUserRepo(), action_repo)

    ctx = await factory.create("access-user")

    assert await ctx.is_disabled() is True


async def test_system_context_delegates_is_disabled() -> None:
    inner = _DummyUserContext(user_id="access-user", disabled=True)

    system_ctx = inner.as_system()

    assert await system_ctx.is_disabled() is True


async def test_unimplemented_context_is_never_disabled() -> None:
    assert await UnimplementedUserContext().is_disabled() is False


async def test_factory_accepts_empty_user_action_dummy() -> None:
    """The dummy action repo keeps a factory-built context enabled."""
    factory = RepoContextFactory(
        _FakeSpyUserRepo(),
        _DummyUserActionRepo(),
    )

    ctx = await factory.create("access-user")

    assert await ctx.is_disabled() is False
