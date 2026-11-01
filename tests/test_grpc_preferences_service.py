"""Tests for :class:`src.grpc_mod.preferences_service.GrpcPreferencesService`.

Wires the **real** adapter to the **real** :class:`PreferencesServiceImpl`
backed by an in-memory repo + permission repo.  That exercises the whole
stack (proto -> adapter -> visitor -> service -> repo) while still running
without Postgres, SpiceDB or any other infrastructure.
"""

from __future__ import annotations

from typing import cast

import grpc
from grpc.aio import ServicerContext

from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.preferences_service import GrpcPreferencesService
from src.grpc_mod.proto.preferences_pb2 import (
    DeletePreferencesRequest,
    GetPreferencesRequest,
    PatchPreferencesRequest,
    PostPreferencesRequest,
    PreferenceScope,
)
from src.services.preferences import PreferencesServiceImpl
from tests.doubles import _FakeSpyPreferencesRepo, InMemoryPermissionRepo, silent_logger
from tests.doubles import _StubUserContextFactory
from tests.doubles.dummies.fake_context import _DummyContext


_USER = "u-1"


def _service() -> tuple[GrpcPreferencesService, _FakeSpyPreferencesRepo]:
    """Build the real gRPC adapter over the real service + fake repo."""
    repo = _FakeSpyPreferencesRepo()
    service = PreferencesServiceImpl(
        preferences_repo=repo,
        permission_repo=InMemoryPermissionRepo(),
        logging_provider=silent_logger,
    )
    adapter = GrpcPreferencesService(
        preferences_service=service,
        log=silent_logger,
        to_grpc=ConvertToGrpcVisitor(),
        context_factory=_StubUserContextFactory(),
    )
    return adapter, repo


def _ctx() -> _DummyContext:
    return _DummyContext()


# ---------------------------------------------------------------------------
# roundtrips
# ---------------------------------------------------------------------------


async def test_post_then_get_roundtrip() -> None:
    service, repo = _service()

    posted = await service.PostPreferences(
        PostPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            preferences_json='{"theme":"dark"}',
            user_id=_USER,
        ),
        cast(ServicerContext, _ctx()),
    )

    assert posted.preferences_json == '{"theme":"dark"}'
    assert posted.key == ""
    assert repo.stored("user", _USER) == {"theme": "dark"}

    fetched = await service.GetPreferences(
        GetPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            user_id=_USER,
        ),
        cast(ServicerContext, _ctx()),
    )
    assert fetched.preferences_json == '{"theme":"dark"}'


async def test_keyed_post_and_get_are_isolated() -> None:
    service, repo = _service()

    await service.PostPreferences(
        PostPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            preferences_json='{"theme":"light"}',
            user_id=_USER,
        ),
        cast(ServicerContext, _ctx()),
    )
    posted = await service.PostPreferences(
        PostPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            preferences_json='{"cols":3}',
            user_id=_USER,
            key="ui",
        ),
        cast(ServicerContext, _ctx()),
    )

    assert posted.key == "ui"
    assert posted.preferences_json == '{"cols":3}'

    fetched = await service.GetPreferences(
        GetPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            user_id=_USER,
            key="ui",
        ),
        cast(ServicerContext, _ctx()),
    )
    assert fetched.preferences_json == '{"cols":3}'

    default = await service.GetPreferences(
        GetPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            user_id=_USER,
        ),
        cast(ServicerContext, _ctx()),
    )
    assert default.preferences_json == '{"theme":"light"}'


async def test_patch_merges_payload() -> None:
    service, repo = _service()
    await service.PostPreferences(
        PostPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            preferences_json='{"a":1}',
            user_id=_USER,
            key="ui",
        ),
        cast(ServicerContext, _ctx()),
    )

    patched = await service.PatchPreferences(
        PatchPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            preferences_json='{"b":2}',
            user_id=_USER,
            key="ui",
        ),
        cast(ServicerContext, _ctx()),
    )

    assert patched.preferences_json == '{"a":1,"b":2}'
    assert repo.stored("user", _USER, "ui") == {"a": 1, "b": 2}


async def test_delete_reports_removed() -> None:
    service, _ = _service()
    await service.PostPreferences(
        PostPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            preferences_json="{}",
            user_id=_USER,
            key="ui",
        ),
        cast(ServicerContext, _ctx()),
    )

    removed = await service.DeletePreferences(
        DeletePreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            user_id=_USER,
            key="ui",
        ),
        cast(ServicerContext, _ctx()),
    )
    assert removed.success is True

    again = await service.DeletePreferences(
        DeletePreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            user_id=_USER,
            key="ui",
        ),
        cast(ServicerContext, _ctx()),
    )
    assert again.success is False


# ---------------------------------------------------------------------------
# validation / errors
# ---------------------------------------------------------------------------


async def test_get_requires_user_id() -> None:
    service, _ = _service()
    context = _ctx()

    await service.GetPreferences(
        GetPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_USER,
            target_id=_USER,
            user_id="",
        ),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "user_id is required"


async def test_unsupported_scope_is_invalid_argument() -> None:
    service, _ = _service()
    context = _ctx()

    await service.GetPreferences(
        GetPreferencesRequest(
            scope=PreferenceScope.PREFERENCE_SCOPE_UNSPECIFIED,
            target_id=_USER,
            user_id=_USER,
        ),
        cast(ServicerContext, context),
    )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert "unsupported preference scope" in (context.details or "")
