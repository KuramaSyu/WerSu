"""Tests for :class:`src.grpc_mod.auth_service.GrpcAuthService`.

The adapter is intentionally thin, so these tests pin the wire
shape end-to-end: gRPC proto in, real :class:`UserAuthServiceImpl`
in the middle, recording fake at the bottom.  Every test asserts
two things -- the gRPC reply and what crossed the boundary into
the service.

Wire shape asserted:

* :meth:`GetUserAuth` resolves by ``user_id``, ``email``, or
  ``discord_id``; returns ``NOT_FOUND`` for misses.
* :meth:`CreateUserAuth` persists ``avatar_url`` on the
  :class:`UserAuthEntity` and ``password_hash`` on the
  :class:`PasswordEntity` (one row per user_id) so a follow-up
  :meth:`FindCredentialByProvider` with ``kind=PASSWORD`` resolves
  to the stored hash.
* :meth:`UpdateUserAuth` with ``avatar_url_set`` writes the new
  value; ``avatar_url_clear`` resets to ``""`` (REST renders that
  as JSON null).
* :exc:`PermissionError` from the service surfaces as
  ``grpc.StatusCode.PERMISSION_DENIED``.  The gRPC adapter does
  no auth checks of its own -- that policy lives in the service
  layer.
"""

from __future__ import annotations
from dataclasses import replace
from datetime import datetime
from google.protobuf.empty_pb2 import Empty
from grpc.aio import ServicerContext
from src.api.errors import FriendlyLoginError
from src.api.other.undefined import UNDEFINED
from src.api.repos.user_auth_repo import UserAuthRepoABC
from src.api.services.user_service import UserFilter
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.password import PasswordEntity
from src.db.entities.user.third_party import ThirdPartyEntity, ThirdPartyFilter
from src.db.entities.user.user_auth import UserAuthEntity
from src.grpc_mod.auth_service import GrpcAuthService
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.auth_pb2 import (
    CredentialKind,
    CreateUserAuthRequest,
    FindCredentialByProviderRequest,
    GetUserAuthRequest,
    RegisterPasskeyRequest,
    RegisterPasskeyResponse,
    UpdateUserAuthRequest,
)
from src.services.user_auth_service import UserAuthServiceImpl
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urlparse
import grpc, logging

from tests.doubles.dummies.fake_context import _DummyContext
from tests.doubles.spys.user_auth_repo_grpc import RecordingUserAuthRepo, _Call, _is_undefined
from tests.doubles.mocks.raising_auth_service import _MockRaisingAuthService


# ---------------------------------------------------------------------
# Recording fake -- the same shape as
# :mod:`tests.test_user_auth_service_impl`, kept inline so this
# file is self-contained.
# ---------------------------------------------------------------------








# ---------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------




def _log_provider(*_args, **_kwargs):
    return logging.getLogger("test.grpc.auth")


def _make_service() -> tuple[GrpcAuthService, RecordingUserAuthRepo]:
    repo = RecordingUserAuthRepo()
    # Use the real service impl, not a fake.  The whole point of
    # these tests is to exercise the gRPC-to-service contract end
    # to end.
    service_impl = UserAuthServiceImpl(repo=repo)
    grpc = GrpcAuthService(
        user_auth_service=service_impl,
        log=_log_provider,
        to_grpc=ConvertToGrpcVisitor(),
    )
    return grpc, repo


def _make_create_request(
    email: str = "alice@example.com",
    username: str = "alice",
    avatar_url: str = "",
    password_hash: str = "argon2id$...",
    display_name: str = "",
) -> CreateUserAuthRequest:
    return CreateUserAuthRequest(
        email=email,
        username=username,
        password_hash=password_hash,
        avatar_url=avatar_url,
        display_name=display_name,
    )


# ---------------------------------------------------------------------
# GetUserAuth
# ---------------------------------------------------------------------


async def test_get_user_auth_resolves_by_user_id() -> None:
    """`GetUserAuth` with `user_id` returns the stored user."""
    service, repo = _make_service()
    create_ctx = _DummyContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url="https://example.com/a.png"),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    get_ctx = _DummyContext()
    response = await service.GetUserAuth(
        GetUserAuthRequest(user_id=user_id),
        cast(ServicerContext, get_ctx),
    )

    assert get_ctx.code is None
    assert response.user.id == user_id
    assert response.user.avatar_url == "https://example.com/a.png"
    # Pin the wire shape -- the adapter called the service with
    # a UserFilter whose only set field is user_id.
    select_calls = [c for c in repo.calls if c.name == "select"]
    assert select_calls
    assert select_calls[-1].args[0].user_id == user_id


async def test_get_user_auth_resolves_by_email() -> None:
    """`GetUserAuth` with `email` returns the user matching that email."""
    service, _ = _make_service()
    create_ctx = _DummyContext()
    await service.CreateUserAuth(
        _make_create_request(
            email="alice@example.com", avatar_url="https://example.com/a.png"
        ),
        cast(ServicerContext, create_ctx),
    )

    get_ctx = _DummyContext()
    response = await service.GetUserAuth(
        GetUserAuthRequest(email="alice@example.com"),
        cast(ServicerContext, get_ctx),
    )

    assert get_ctx.code is None
    assert response.user.email == "alice@example.com"


async def test_get_user_auth_resolves_by_discord_id() -> None:
    """`GetUserAuth` with `discord_id` returns the matching user."""
    service, repo = _make_service()
    seeded = await repo.insert(
        UserAuthEntity(
            id=UNDEFINED,
            username="bob",
            email="bob@example.com",
            avatar="https://example.com/b.png",
            third_parties=[ThirdPartyEntity(
                provider="discord",
                provider_user_id="987654321",
            )],
        )
    )

    get_ctx = _DummyContext()
    response = await service.GetUserAuth(
        GetUserAuthRequest(discord_id=987654321),
        cast(ServicerContext, get_ctx),
    )

    assert get_ctx.code is None
    assert response.user.id == seeded.id


async def test_get_user_auth_avatar_url_is_discord_cdn_url() -> None:
    """`GetUserAuth` builds a Discord CDN URL from discord_id + avatar hash.

    Pins the wire shape: a Discord-linked user stores an avatar
    hash on the entity, but the gRPC layer must return a fully
    qualified URL so the frontend can drop it into ``<img src>``
    without extra work.
    """

    service, repo = _make_service()
    await repo.insert(
        UserAuthEntity(
            id=UNDEFINED,
            username="carol",
            email="carol@example.com",
            avatar="abcdef0123",  # raw avatar hash, not a URL
            third_parties=[ThirdPartyEntity(
                provider="discord",
                provider_user_id="123456789",
            )],
        )
    )

    ctx = _DummyContext()
    response = await service.GetUserAuth(
        GetUserAuthRequest(discord_id=123456789),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    parsed = urlparse(response.user.avatar_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "cdn.discordapp.com"
    assert parsed.path == "/avatars/123456789/abcdef0123"


async def test_get_user_auth_passes_through_existing_url_avatar() -> None:
    """`GetUserAuth` returns a pre-existing URL avatar unchanged.

    Only raw hashes get rewritten into Discord CDN URLs.  If the
    stored avatar is already a URL -- e.g. a custom-uploaded
    avatar or a Google profile picture -- the gRPC layer must
    hand it back verbatim.
    """
    service, repo = _make_service()
    await repo.insert(
        UserAuthEntity(
            id=UNDEFINED,
            username="dave",
            email="dave@example.com",
            avatar="https://cdn.example.com/dave.png",
            third_parties=[ThirdPartyEntity(
                provider="discord",
                provider_user_id="222222222",
            )],
        )
    )

    ctx = _DummyContext()
    response = await service.GetUserAuth(
        GetUserAuthRequest(discord_id=222222222),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    assert response.user.avatar_url == "https://cdn.example.com/dave.png"


async def test_get_user_auth_returns_not_found_for_unknown_id() -> None:
    """`GetUserAuth` for an unknown user_id returns `NOT_FOUND`."""
    service, _ = _make_service()
    ctx = _DummyContext()

    response = await service.GetUserAuth(
        GetUserAuthRequest(user_id="does-not-exist"),
        cast(ServicerContext, ctx),
    )

    assert ctx.code == grpc.StatusCode.NOT_FOUND
    assert response.user.id == ""


# ---------------------------------------------------------------------
# CreateUserAuth
# ---------------------------------------------------------------------


async def test_create_user_auth_round_trips_avatar_url() -> None:
    """`CreateUserAuth` persists the avatar_url; `GetUserAuth` returns it."""
    service, _ = _make_service()
    ctx = _DummyContext()

    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url="https://example.com/a.png"),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    assert (
        create_response.user.avatar_url == "https://example.com/a.png"
    )

    get_ctx = _DummyContext()
    response = await service.GetUserAuth(
        GetUserAuthRequest(user_id=create_response.user.id),
        cast(ServicerContext, get_ctx),
    )
    assert response.user.avatar_url == "https://example.com/a.png"


async def test_create_user_auth_with_empty_avatar_url_stores_empty_string() -> None:
    """`CreateUserAuth` with avatar_url="" persists the empty string."""
    service, repo = _make_service()
    ctx = _DummyContext()

    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url=""),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    assert create_response.user.avatar_url == ""

    persisted = await repo.select(
        UserFilter(user_id=create_response.user.id)
    )
    assert persisted is not None
    assert persisted.avatar == ""


async def test_create_user_auth_persists_password_hash() -> None:
    """`CreateUserAuth` writes the ``password_hash`` to ``auth.password``.

    Pins the signup -> signin contract: REST sends a hashed
    argon2id string; the gRPC adapter must store it on the
    password row keyed by ``user_id``.  Without this call, the
    follow-up :meth:`FindCredentialByProvider` lookup returns
    ``NOT_FOUND`` and REST maps that to ``401 invalid email or
    password``.
    """
    service, repo = _make_service()
    ctx = _DummyContext()
    phc = "$argon2id$v=19$m=65536,t=3,p=4$abc$def"

    create_response = await service.CreateUserAuth(
        _make_create_request(password_hash=phc),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    stored = await repo.find_password(create_response.user.id)
    assert stored is not None
    assert stored.password_hash == phc
    # Wire shape: the adapter called upsert_password exactly once.
    upsert_calls = [c for c in repo.calls if c.name == "upsert_password"]
    assert len(upsert_calls) == 1
    assert upsert_calls[0].args[0].user_id == create_response.user.id


async def test_create_user_auth_without_password_hash_skips_password_row() -> None:
    """`CreateUserAuth` with an empty `password_hash` writes no password row.

    OAuth-only signups (`/auth/signup` is not the only signup
    path) leave the password row alone so the user can add one
    later via :meth:`LinkCredential`.
    """
    service, repo = _make_service()
    ctx = _DummyContext()

    create_response = await service.CreateUserAuth(
        _make_create_request(password_hash=""),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    stored = await repo.find_password(create_response.user.id)
    assert stored is None
    assert not any(c.name == "upsert_password" for c in repo.calls)


async def test_create_user_auth_then_find_credential_by_password_email() -> None:
    """Round-trip: signup -> signin returns the stored hash.

    Drives the same flow REST's `POST /auth/login` exercises:
    `CreateUserAuth` writes the password row, then
    `FindCredentialByProvider` with kind=PASSWORD and
    identifier=email resolves it back to the same hash REST
    stored.
    """
    service, repo = _make_service()
    phc = "$argon2id$v=19$m=65536,t=3,p=4$abc$def"

    create_ctx = _DummyContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(
            email="alice@example.com", password_hash=phc
        ),
        cast(ServicerContext, create_ctx),
    )
    assert create_ctx.code is None
    user_id = create_response.user.id

    find_ctx = _DummyContext()
    find_response = await service.FindCredentialByProvider(
        FindCredentialByProviderRequest(
            kind=CredentialKind.CREDENTIAL_KIND_PASSWORD,
            email="alice@example.com",
        ),
        cast(ServicerContext, find_ctx),
    )
    assert find_ctx.code is None
    assert find_response.credential.password_hash == phc
    assert find_response.user.id == user_id
    # Sanity-check: find_password on the repo returns the same row.
    assert (await repo.find_password(user_id)).password_hash == phc


# ---------------------------------------------------------------------
# UpdateUserAuth
# ---------------------------------------------------------------------


async def test_update_user_auth_avatar_url_set_writes_new_value() -> None:
    """`UpdateUserAuth` with `avatar_url_set` overwrites the avatar."""
    service, _ = _make_service()
    create_ctx = _DummyContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url="https://example.com/old.png"),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    update_ctx = _DummyContext()
    update_response = await service.UpdateUserAuth(
        UpdateUserAuthRequest(
            user_id=user_id,
            requester_id=user_id,
            avatar_url_set="https://example.com/new.png",
        ),
        cast(ServicerContext, update_ctx),
    )

    assert update_ctx.code is None
    assert update_response.user.avatar_url == "https://example.com/new.png"


async def test_update_user_auth_avatar_url_clear_resets_to_empty_string() -> None:
    """`UpdateUserAuth` with `avatar_url_clear` sets avatar to ``""``."""
    service, _ = _make_service()
    create_ctx = _DummyContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url="https://example.com/start.png"),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    update_ctx = _DummyContext()
    update_response = await service.UpdateUserAuth(
        UpdateUserAuthRequest(
            user_id=user_id,
            requester_id=user_id,
            avatar_url_clear=Empty(),  # type: ignore[arg-type]
        ),
        cast(ServicerContext, update_ctx),
    )

    assert update_ctx.code is None
    assert update_response.user.avatar_url == ""


async def test_update_user_auth_propagates_permission_error_from_service() -> None:
    """A `PermissionError` from the service maps to `PERMISSION_DENIED`."""
    service, _ = _make_service()
    create_ctx = _DummyContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url="https://example.com/a.png"),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    update_ctx = _DummyContext()
    response = await service.UpdateUserAuth(
        UpdateUserAuthRequest(
            user_id=user_id,
            requester_id="someone-else",
            avatar_url_set="https://example.com/hijack.png",
        ),
        cast(ServicerContext, update_ctx),
    )

    assert update_ctx.code == grpc.StatusCode.PERMISSION_DENIED
    assert response.user.id == ""


async def test_update_user_auth_rejects_missing_user_id() -> None:
    """`UpdateUserAuth` with no `user_id` returns `INVALID_ARGUMENT`."""
    service, _ = _make_service()
    ctx = _DummyContext()

    response = await service.UpdateUserAuth(
        UpdateUserAuthRequest(
            user_id="",
            requester_id="",
            avatar_url_set="https://example.com/x.png",
        ),
        cast(ServicerContext, ctx),
    )

    assert ctx.code == grpc.StatusCode.INVALID_ARGUMENT
    assert response.user.id == ""


# ---------------------------------------------------------------------
# Passkey RPCs
# ---------------------------------------------------------------------


async def test_register_passkey_routes_through_passkey_service() -> None:
    """`RegisterPasskey` calls `passkey_service.register_passkey`."""
    service, repo = _make_service()
    create_ctx = _DummyContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    ctx = _DummyContext()
    response: RegisterPasskeyResponse = await service.RegisterPasskey(
        RegisterPasskeyRequest(
            user_id=user_id,
            requester_id=user_id,
            credential_id=b"cred-1",
            public_key=b"pub-1",
            friendly_name="My key",
        ),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    assert response.passkey.id != ""
    assert response.passkey.user_id == user_id
    assert response.passkey.friendly_name == "My key"
    # Wire shape: the repo saw an insert_passkey call.
    assert any(c.name == "insert_passkey" for c in repo.calls)


# ---------------------------------------------------------------------
# FriendlyLoginError -> ALREADY_EXISTS mapping
# ---------------------------------------------------------------------




def _make_raising_service(field: str, value: str) -> GrpcAuthService:
    """Build a gRPC adapter backed by a service that always rejects."""
    return GrpcAuthService(
        user_auth_service=cast(Any, _MockRaisingAuthService(field, value)),
        log=_log_provider,
        to_grpc=ConvertToGrpcVisitor(),
    )


async def test_create_user_auth_returns_already_exists_for_duplicate_email() -> None:
    """A `FriendlyLoginError("email", ...)` from the service maps to ALREADY_EXISTS.

    Pins the wire shape: the gRPC reply carries no user but the
    status code is ALREADY_EXISTS and the details string mentions
    the offending field.  REST renders this back to the browser
    so the signup form can highlight the email input.
    """
    service = _make_raising_service("email", "alice@example.com")
    ctx = _DummyContext()

    response = await service.CreateUserAuth(
        _make_create_request(email="alice@example.com"),
        cast(ServicerContext, ctx),
    )

    assert ctx.code == grpc.StatusCode.ALREADY_EXISTS
    assert response.user.id == ""
    assert ctx.details is not None
    assert "email" in ctx.details


async def test_create_user_auth_returns_already_exists_for_duplicate_username() -> None:
    """A `FriendlyLoginError("username", ...)` maps to ALREADY_EXISTS too."""
    service = _make_raising_service("username", "alice")
    ctx = _DummyContext()

    response = await service.CreateUserAuth(
        _make_create_request(),
        cast(ServicerContext, ctx),
    )

    assert ctx.code == grpc.StatusCode.ALREADY_EXISTS
    assert response.user.id == ""
    assert ctx.details is not None
    assert "username" in ctx.details


async def test_create_user_auth_persists_display_name() -> None:
    """`CreateUserAuth` writes the optional `display_name` to the auth row."""
    service, repo = _make_service()
    ctx = _DummyContext()

    response = await service.CreateUserAuth(
        _make_create_request(display_name="Alice in Wonderland"),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    assert response.user.display_name == "Alice in Wonderland"
    persisted = await repo.select(
        UserFilter(user_id=response.user.id)
    )
    assert persisted is not None
    assert persisted.display_name == "Alice in Wonderland"


async def test_create_user_auth_without_display_name_stores_empty_string() -> None:
    """`CreateUserAuth` with no `display_name` stores an empty string.

    Empty maps to JSON null in REST so the frontend can tell the
    field is unset without overloading on the literal "".
    """
    service, _ = _make_service()
    ctx = _DummyContext()

    response = await service.CreateUserAuth(
        _make_create_request(),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    assert response.user.display_name == ""


async def test_update_user_auth_display_name_set_writes_value() -> None:
    """`UpdateUserAuth` with `display_name_set` writes the new value."""
    service, _ = _make_service()
    create_ctx = _DummyContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    update_ctx = _DummyContext()
    update_response = await service.UpdateUserAuth(
        UpdateUserAuthRequest(
            user_id=user_id,
            requester_id=user_id,
            display_name_set="Alice the Brave",
        ),
        cast(ServicerContext, update_ctx),
    )

    assert update_ctx.code is None
    assert update_response.user.display_name == "Alice the Brave"


async def test_update_user_auth_display_name_clear_resets_to_empty() -> None:
    """`UpdateUserAuth` with `display_name_clear` resets the field to empty."""
    service, _ = _make_service()
    create_ctx = _DummyContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(display_name="Some Name"),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id
    assert create_response.user.display_name == "Some Name"

    update_ctx = _DummyContext()
    update_response = await service.UpdateUserAuth(
        UpdateUserAuthRequest(
            user_id=user_id,
            requester_id=user_id,
            display_name_clear=Empty(),
        ),
        cast(ServicerContext, update_ctx),
    )

    assert update_ctx.code is None
    assert update_response.user.display_name == ""


async def test_update_user_auth_returns_already_exists_for_duplicate_email() -> None:
    """A `FriendlyLoginError` from `update_user` also maps to ALREADY_EXISTS."""
    repo = RecordingUserAuthRepo()
    # Seed an existing user so update_user has something to collide on.
    await repo.insert(
        UserAuthEntity(email="alice@example.com", username="alice")
    )
    service = GrpcAuthService(
        user_auth_service=cast(Any, _MockRaisingAuthService("email", "bob@x.com")),
        log=_log_provider,
        to_grpc=ConvertToGrpcVisitor(),
    )
    user_id = next(iter(repo._by_id.values())).id  # type: ignore[attr-defined]

    ctx = _DummyContext()
    response = await service.UpdateUserAuth(
        UpdateUserAuthRequest(
            user_id=user_id,
            requester_id=user_id,
            email_set="bob@x.com",
        ),
        cast(ServicerContext, ctx),
    )

    assert ctx.code == grpc.StatusCode.ALREADY_EXISTS
    assert response.user.id == ""
