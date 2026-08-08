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
  :class:`UserAuthEntity`.
* :meth:`UpdateUserAuth` with ``avatar_url_set`` writes the new
  value; ``avatar_url_clear`` resets to ``""`` (REST renders that
  as JSON null).
* :exc:`PermissionError` from the service surfaces as
  ``grpc.StatusCode.PERMISSION_DENIED``.  The gRPC adapter does
  no auth checks of its own -- that policy lives in the service
  layer.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional, cast

import grpc
from google.protobuf.empty_pb2 import Empty
from grpc.aio import ServicerContext

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
    CreateUserAuthRequest,
    GetUserAuthRequest,
    RegisterPasskeyRequest,
    RegisterPasskeyResponse,
    UpdateUserAuthRequest,
)
from src.services.user_auth_service import UserAuthServiceImpl


# ---------------------------------------------------------------------
# Recording fake -- the same shape as
# :mod:`tests.test_user_auth_service_impl`, kept inline so this
# file is self-contained.
# ---------------------------------------------------------------------


class _Call:
    """One recorded call on the fake repo."""

    def __init__(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.name = name
        self.args = args
        self.kwargs = kwargs


class RecordingUserAuthRepo(UserAuthRepoABC):
    """In-memory + recording :class:`UserAuthRepoABC`."""

    def __init__(self) -> None:
        self.calls: List[_Call] = []
        self._counter = 0
        self._by_id: Dict[str, UserAuthEntity] = {}
        self._by_email: Dict[str, str] = {}
        self._passwords: Dict[str, PasswordEntity] = {}
        self._passkeys: Dict[str, PasskeyEntity] = {}
        self._third_parties: Dict[str, ThirdPartyEntity] = {}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(_Call(name, *args, **kwargs))

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    async def insert(self, user: UserAuthEntity) -> UserAuthEntity:
        self._record("insert", user)
        uid = self._next_id("u")
        created = replace(user, id=uid)
        self._by_id[uid] = created
        if created.email is not None:
            self._by_email[created.email] = uid
        for tp in user.third_parties:
            await self.insert_third_party(replace(tp, user_id=uid))
        return await self.select(UserFilter(user_id=uid)) or created

    async def select(
        self, filter: UserFilter
    ) -> Optional[UserAuthEntity]:
        self._record("select", filter)
        if filter.is_empty():
            return None
        for user in self._by_id.values():
            if (
                not _is_undefined(filter.user_id)
                and user.id != filter.user_id
            ):
                continue
            if (
                not _is_undefined(filter.email)
                and user.email != filter.email
            ):
                continue
            if (
                not _is_undefined(filter.discord_id)
                and user.discord_id() != filter.discord_id
            ):
                continue
            return replace(user, third_parties=list(user.third_parties))
        return None

    async def update(self, user: UserAuthEntity) -> UserAuthEntity:
        self._record("update", user)
        existing = self._by_id.get(user.id)  # type: ignore[arg-type]
        if existing is None:
            raise ValueError(f"User not found: {user.id}")
        merged = existing
        for field_name in ("avatar", "username", "email", "type"):
            new_val = getattr(user, field_name, UNDEFINED)
            if not _is_undefined(new_val):
                merged = replace(merged, **{field_name: new_val})
        if user.third_parties:
            merged = replace(merged, third_parties=list(user.third_parties))
        self._by_id[merged.id] = merged  # type: ignore[arg-type]
        return (
            await self.select(UserFilter(user_id=merged.id)) or merged  # type: ignore[arg-type]
        )

    async def upsert_password(
        self, password: PasswordEntity
    ) -> PasswordEntity:
        self._record("upsert_password", password)
        self._passwords[password.user_id] = password
        return password

    async def find_password(self, user_id: str) -> Optional[PasswordEntity]:
        self._record("find_password", user_id)
        return self._passwords.get(user_id)

    async def insert_passkey(self, passkey: PasskeyEntity) -> PasskeyEntity:
        self._record("insert_passkey", passkey)
        pid = self._next_id("pk")
        stored = replace(passkey, id=pid)
        self._passkeys[pid] = stored
        return stored

    async def find_passkey(
        self, credential_id: bytes
    ) -> Optional[PasskeyEntity]:
        self._record("find_passkey", credential_id)
        for pk in self._passkeys.values():
            if pk.credential_id == credential_id:
                return pk
        return None

    async def find_passkey_by_id(
        self, passkey_id: str
    ) -> Optional[PasskeyEntity]:
        self._record("find_passkey_by_id", passkey_id)
        return self._passkeys.get(passkey_id)

    async def list_passkeys(
        self, user_id: str, include_revoked: bool = False
    ) -> List[PasskeyEntity]:
        self._record("list_passkeys", user_id, include_revoked=include_revoked)
        return [
            pk
            for pk in self._passkeys.values()
            if pk.user_id == user_id
            and (include_revoked or pk.revoked_at is None)
        ]

    async def update_passkey_sign_count(
        self,
        passkey_id: str,
        new_sign_count: int,
    ) -> PasskeyEntity:
        self._record(
            "update_passkey_sign_count", passkey_id, new_sign_count
        )
        pk = self._passkeys[passkey_id]
        if new_sign_count <= pk.sign_count:
            raise ValueError(
                f"new_sign_count ({new_sign_count}) must be > "
                f"current ({pk.sign_count})"
            )
        updated = replace(pk, sign_count=new_sign_count)
        self._passkeys[passkey_id] = updated
        return updated

    async def revoke_passkey(self, passkey_id: str) -> PasskeyEntity:
        self._record("revoke_passkey", passkey_id)
        pk = self._passkeys[passkey_id]
        from datetime import datetime
        revoked = replace(pk, revoked_at=datetime.now())
        self._passkeys[passkey_id] = revoked
        return revoked

    async def insert_third_party(
        self, third: ThirdPartyEntity
    ) -> ThirdPartyEntity:
        self._record("insert_third_party", third)
        tid = self._next_id("tp")
        stored = replace(third, id=tid)
        self._third_parties[tid] = stored
        return stored

    async def find_third_party(
        self, filter: ThirdPartyFilter
    ) -> List[ThirdPartyEntity]:
        self._record("find_third_party", filter)
        if filter.is_empty():
            return []
        out: List[ThirdPartyEntity] = []
        for tp in self._third_parties.values():
            if (
                not _is_undefined(filter.id)
                and str(tp.id or "") != filter.id
            ):
                continue
            if (
                not _is_undefined(filter.user_id)
                and tp.user_id != filter.user_id
            ):
                continue
            if (
                not _is_undefined(filter.provider)
                and tp.provider != filter.provider
            ):
                continue
            if (
                not _is_undefined(filter.provider_user_id)
                and tp.provider_user_id != filter.provider_user_id
            ):
                continue
            out.append(tp)
        return out

    async def delete_third_party(self, third_party_id: str) -> bool:
        self._record("delete_third_party", third_party_id)
        return self._third_parties.pop(third_party_id, None) is not None


def _is_undefined(value: Any) -> bool:
    return value is UNDEFINED


# ---------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------


class _FakeContext:
    def __init__(self) -> None:
        self.code: Optional[grpc.StatusCode] = None
        self.details: Optional[str] = None

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


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
) -> CreateUserAuthRequest:
    return CreateUserAuthRequest(
        email=email,
        username=username,
        password_hash="argon2id$...",
        avatar_url=avatar_url,
    )


# ---------------------------------------------------------------------
# GetUserAuth
# ---------------------------------------------------------------------


async def test_get_user_auth_resolves_by_user_id() -> None:
    """`GetUserAuth` with `user_id` returns the stored user."""
    service, repo = _make_service()
    create_ctx = _FakeContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url="https://example.com/a.png"),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    get_ctx = _FakeContext()
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
    create_ctx = _FakeContext()
    await service.CreateUserAuth(
        _make_create_request(
            email="alice@example.com", avatar_url="https://example.com/a.png"
        ),
        cast(ServicerContext, create_ctx),
    )

    get_ctx = _FakeContext()
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

    get_ctx = _FakeContext()
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
    from urllib.parse import urlparse

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

    ctx = _FakeContext()
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

    ctx = _FakeContext()
    response = await service.GetUserAuth(
        GetUserAuthRequest(discord_id=222222222),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    assert response.user.avatar_url == "https://cdn.example.com/dave.png"


async def test_get_user_auth_returns_not_found_for_unknown_id() -> None:
    """`GetUserAuth` for an unknown user_id returns `NOT_FOUND`."""
    service, _ = _make_service()
    ctx = _FakeContext()

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
    ctx = _FakeContext()

    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url="https://example.com/a.png"),
        cast(ServicerContext, ctx),
    )

    assert ctx.code is None
    assert (
        create_response.user.avatar_url == "https://example.com/a.png"
    )

    get_ctx = _FakeContext()
    response = await service.GetUserAuth(
        GetUserAuthRequest(user_id=create_response.user.id),
        cast(ServicerContext, get_ctx),
    )
    assert response.user.avatar_url == "https://example.com/a.png"


async def test_create_user_auth_with_empty_avatar_url_stores_empty_string() -> None:
    """`CreateUserAuth` with avatar_url="" persists the empty string."""
    service, repo = _make_service()
    ctx = _FakeContext()

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


# ---------------------------------------------------------------------
# UpdateUserAuth
# ---------------------------------------------------------------------


async def test_update_user_auth_avatar_url_set_writes_new_value() -> None:
    """`UpdateUserAuth` with `avatar_url_set` overwrites the avatar."""
    service, _ = _make_service()
    create_ctx = _FakeContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url="https://example.com/old.png"),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    update_ctx = _FakeContext()
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
    create_ctx = _FakeContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url="https://example.com/start.png"),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    update_ctx = _FakeContext()
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
    create_ctx = _FakeContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(avatar_url="https://example.com/a.png"),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    update_ctx = _FakeContext()
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
    ctx = _FakeContext()

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
    create_ctx = _FakeContext()
    create_response = await service.CreateUserAuth(
        _make_create_request(),
        cast(ServicerContext, create_ctx),
    )
    user_id = create_response.user.id

    ctx = _FakeContext()
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
    # Wire shape: the repo saw an `insert_passkey` call.
    assert any(c.name == "insert_passkey" for c in repo.calls)
