"""Tests for the concrete auth-side service implementations.

Strategy: every test wires the real
:class:`UserAuthServiceImpl` / :class:`UserPasswordAuthServiceImpl`
/ :class:`UserPasskeyAuthServiceImpl` against a
**recording fake** that implements the same contract as
:class:`UserAuthRepoABC` but records every call and stores rows
in memory.  This pins both the *behaviour* (the impl actually
delegates to the repo) and the *protocol* (the right arguments
cross the boundary).

Why not test the Postgres impl?  The repo is a thin SQL mapper;
the interesting policy lives in the service layer.  The only way
to know if the Postgres SQL is correct is to run it against a
real database, which integration tests already cover.

Wire shape asserted:

* :meth:`UserAuthServiceImpl.update_user` raises
  :exc:`PermissionError` when ``requester_id != user.id`` --
  gRPC layer no longer does this check.
* :meth:`UserPasswordAuthServiceImpl.set_user_password`
  upserts on the same user_id.
* :meth:`UserPasskeyAuthServiceImpl.update_sign_count` rejects
  a non-monotonic counter.
* :meth:`UserPasskeyAuthServiceImpl.revoke_passkey` is
  idempotent.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

import pytest

from src.api.other.undefined import UNDEFINED
from src.api.repos.user_auth_repo import UserAuthRepoABC
from src.api.services.user_auth_service import (
    UserAuthServiceABC,
    UserPasskeyAuthServiceABC,
    UserPasswordAuthServiceABC,
)
from src.api.services.user_service import UserFilter
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.password import PasswordEntity
from src.db.entities.user.third_party import ThirdPartyEntity, ThirdPartyFilter
from src.db.entities.user.user_auth import UserAuthEntity
from src.services.user_auth_service import (
    UserAuthServiceImpl,
    UserPasskeyAuthServiceImpl,
    UserPasswordAuthServiceImpl,
)


# ---------------------------------------------------------------------
# Recording fake
# ---------------------------------------------------------------------


@dataclass
class _Call:
    """A single recorded call on the fake repo."""

    name: str
    args: tuple
    kwargs: dict


@dataclass
class RecordingUserAuthRepo(UserAuthRepoABC):
    """In-memory + recording :class:`UserAuthRepoABC`.

    Stores rows in ``self._by_id`` / ``self._passwords`` /
    ``self._passkeys`` / ``self._third_parties`` so the real impl
    can run against a working backend, and records every method
    call in :attr:`calls` so the test can assert what the impl
    delegated.
    """

    calls: List[_Call] = field(default_factory=list)
    _counter: int = 0
    _by_id: Dict[str, UserAuthEntity] = field(default_factory=dict)
    _by_email: Dict[str, str] = field(default_factory=dict)
    _by_discord_provider: Dict[str, str] = field(default_factory=dict)
    _passwords: Dict[str, PasswordEntity] = field(default_factory=dict)
    _passkeys: Dict[str, PasskeyEntity] = field(default_factory=dict)
    _third_parties: Dict[str, ThirdPartyEntity] = field(default_factory=dict)

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(_Call(name=name, args=args, kwargs=kwargs))

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    # ---- user -------------------------------------------------------

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

    # ---- password --------------------------------------------------

    async def upsert_password(
        self, password: PasswordEntity
    ) -> PasswordEntity:
        self._record("upsert_password", password)
        self._passwords[password.user_id] = password
        return password

    async def find_password(self, user_id: str) -> Optional[PasswordEntity]:
        self._record("find_password", user_id)
        return self._passwords.get(user_id)

    # ---- passkey ---------------------------------------------------

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
        out = []
        for pk in self._passkeys.values():
            if pk.user_id != user_id:
                continue
            if not include_revoked and pk.revoked_at is not None:
                continue
            out.append(pk)
        return out

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
        if pk.revoked_at is None:
            revoked = replace(pk, revoked_at=datetime.datetime.now())
            self._passkeys[passkey_id] = revoked
            return revoked
        return pk

    # ---- third party ----------------------------------------------

    async def insert_third_party(
        self, third: ThirdPartyEntity
    ) -> ThirdPartyEntity:
        self._record("insert_third_party", third)
        tid = self._next_id("tp")
        stored = replace(third, id=tid)
        self._third_parties[tid] = stored
        key = (stored.provider, stored.provider_user_id)
        self._by_discord_provider[key[1]] = (
            stored.user_id if stored.provider == "discord" else stored.user_id
        )
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
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def repo() -> RecordingUserAuthRepo:
    return RecordingUserAuthRepo()


@pytest.fixture
def auth_service(repo: RecordingUserAuthRepo) -> UserAuthServiceABC:
    return UserAuthServiceImpl(repo=repo)


@pytest.fixture
def password_service(
    repo: RecordingUserAuthRepo,
) -> UserPasswordAuthServiceABC:
    return UserPasswordAuthServiceImpl(repo=repo)


@pytest.fixture
def passkey_service(
    repo: RecordingUserAuthRepo,
) -> UserPasskeyAuthServiceABC:
    return UserPasskeyAuthServiceImpl(repo=repo)


# ---------------------------------------------------------------------
# UserAuthServiceImpl
# ---------------------------------------------------------------------


async def test_auth_service_create_user_delegates_to_repo_insert(
    auth_service: UserAuthServiceABC, repo: RecordingUserAuthRepo
) -> None:
    """`create_user` calls `repo.insert(user)` and returns the persisted row."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )

    # The real impl may issue a follow-up `select` to attach the
    # persisted third_parties; what we care about is that an
    # `insert` landed at the start of the call sequence.
    assert repo.calls[0].name == "insert"
    assert seeded.id != UNDEFINED
    assert seeded.username == "alice"


async def test_auth_service_get_user_delegates_to_repo_select(
    auth_service: UserAuthServiceABC, repo: RecordingUserAuthRepo
) -> None:
    """`get_user` calls `repo.select(filter)`."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )

    got = await auth_service.get_user(UserFilter(email="a@b.c"))

    assert any(c.name == "select" for c in repo.calls)
    assert got is not None
    assert got.id == seeded.id


async def test_auth_service_update_user_enforces_requester_id(
    auth_service: UserAuthServiceABC,
) -> None:
    """`update_user` raises PermissionError when requester_id mismatches."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )

    with pytest.raises(PermissionError):
        await auth_service.update_user(
            UserAuthEntity(id=seeded.id, username="hijacked"),
            requester_id="someone-else",
        )


async def test_auth_service_update_user_delegates_to_repo_when_authorized(
    auth_service: UserAuthServiceABC, repo: RecordingUserAuthRepo
) -> None:
    """`update_user` calls `repo.update` when the requester check passes."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    repo.calls.clear()

    await auth_service.update_user(
        UserAuthEntity(id=seeded.id, username="alice2", email=UNDEFINED),
        requester_id=seeded.id,
    )

    assert any(c.name == "update" for c in repo.calls)


async def test_auth_service_update_user_writes_only_set_fields(
    auth_service: UserAuthServiceABC,
) -> None:
    """`update_user` leaves UNDEFINED fields alone."""
    seeded = await auth_service.create_user(
        UserAuthEntity(
            id=UNDEFINED,
            username="alice",
            email="alice@example.com",
            avatar="old.png",
        )
    )

    updated = await auth_service.update_user(
        UserAuthEntity(
            id=seeded.id,
            username="alice2",
            email=UNDEFINED,
            avatar=UNDEFINED,
        ),
        requester_id=seeded.id,
    )

    assert updated.username == "alice2"
    assert updated.email == "alice@example.com"
    assert updated.avatar == "old.png"


async def test_auth_service_passwords_sub_service_returns_password_impl(
    auth_service: UserAuthServiceABC,
) -> None:
    assert isinstance(auth_service.passwords, UserPasswordAuthServiceImpl)


async def test_auth_service_passkeys_sub_service_returns_passkey_impl(
    auth_service: UserAuthServiceABC,
) -> None:
    assert isinstance(auth_service.passkeys, UserPasskeyAuthServiceImpl)


# ---------------------------------------------------------------------
# UserPasswordAuthServiceImpl
# ---------------------------------------------------------------------


async def test_password_set_user_password_inserts_on_first_call(
    auth_service: UserAuthServiceABC,
    password_service: UserPasswordAuthServiceABC,
    repo: RecordingUserAuthRepo,
) -> None:
    """First call writes a row."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    repo.calls.clear()

    pw = await password_service.set_user_password(
        seeded.id, seeded.id, "argon2id$..."
    )

    assert any(c.name == "upsert_password" for c in repo.calls)
    assert pw.user_id == seeded.id
    assert pw.password_hash == "argon2id$..."


async def test_password_set_user_password_upserts_on_second_call(
    auth_service: UserAuthServiceABC,
    password_service: UserPasswordAuthServiceABC,
) -> None:
    """A second call overwrites the existing row rather than raising."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    await password_service.set_user_password(seeded.id, seeded.id, "hash-1")

    # Must not raise on PK collision
    pw = await password_service.set_user_password(
        seeded.id, seeded.id, "hash-2"
    )

    assert pw.password_hash == "hash-2"
    found = await password_service.find_password(seeded.id)
    assert found is not None
    assert found.password_hash == "hash-2"


async def test_password_set_user_password_rejects_requester_mismatch(
    auth_service: UserAuthServiceABC,
    password_service: UserPasswordAuthServiceABC,
) -> None:
    """`set_user_password` raises PermissionError on requester mismatch."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )

    with pytest.raises(PermissionError):
        await password_service.set_user_password(
            seeded.id, "someone-else", "hash"
        )


async def test_password_set_user_password_via_auth_service_property(
    auth_service: UserAuthServiceABC,
) -> None:
    """`auth_service.passwords.set_user_password(...)` reaches the same impl."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )

    pw = await auth_service.passwords.set_user_password(
        seeded.id, seeded.id, "hash"
    )

    assert pw.password_hash == "hash"


# ---------------------------------------------------------------------
# UserPasskeyAuthServiceImpl
# ---------------------------------------------------------------------


def _passkey(user_id: str = "placeholder") -> PasskeyEntity:
    return PasskeyEntity(
        id=UNDEFINED,
        user_id=user_id,
        credential_id=b"cred-id",
        public_key=b"public-key",
        sign_count=0,
        transports=[],
        friendly_name="Test key",
    )


async def test_passkey_register_persists_with_assigned_id(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
    repo: RecordingUserAuthRepo,
) -> None:
    """`register_passkey` assigns a server-side id and stamps user_id."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    repo.calls.clear()

    stored = await passkey_service.register_passkey(
        seeded.id, seeded.id, _passkey()
    )

    assert any(c.name == "insert_passkey" for c in repo.calls)
    assert stored.id != UNDEFINED
    assert stored.user_id == seeded.id


async def test_passkey_register_rejects_requester_mismatch(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
) -> None:
    """`register_passkey` raises PermissionError on requester mismatch."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )

    with pytest.raises(PermissionError):
        await passkey_service.register_passkey(
            seeded.id, "someone-else", _passkey()
        )


async def test_passkey_find_resolves_by_credential_id(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
    repo: RecordingUserAuthRepo,
) -> None:
    """`find_passkey` calls `repo.find_passkey(credential_id)`."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    stored = await passkey_service.register_passkey(
        seeded.id, seeded.id, _passkey()
    )
    repo.calls.clear()

    found = await passkey_service.find_passkey(stored.credential_id)

    assert any(c.name == "find_passkey" for c in repo.calls)
    assert found is not None
    assert found.id == stored.id


async def test_passkey_list_filters_by_user(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
) -> None:
    """`list_passkeys(user_id)` returns only that user's keys."""
    alice = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    bob = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="bob", email="b@b.c")
    )
    await passkey_service.register_passkey(alice.id, alice.id, _passkey())
    bob_key = replace(_passkey(), user_id=bob.id, credential_id=b"bob-cred")
    await passkey_service.register_passkey(bob.id, bob.id, bob_key)

    alice_keys = await passkey_service.list_passkeys(alice.id)
    bob_keys = await passkey_service.list_passkeys(bob.id)

    assert len(alice_keys) == 1
    assert alice_keys[0].user_id == alice.id
    assert len(bob_keys) == 1
    assert bob_keys[0].user_id == bob.id


async def test_passkey_list_excludes_revoked_by_default(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
) -> None:
    """`list_passkeys(user_id)` hides revoked keys."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    stored = await passkey_service.register_passkey(
        seeded.id, seeded.id, _passkey()
    )
    await passkey_service.revoke_passkey(stored.id, seeded.id)

    assert await passkey_service.list_passkeys(seeded.id) == []
    assert (
        len(
            await passkey_service.list_passkeys(
                seeded.id, include_revoked=True
            )
        )
        == 1
    )


async def test_passkey_update_sign_count_requires_strict_increase(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
) -> None:
    """`update_sign_count` rejects a non-monotonic counter."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    stored = await passkey_service.register_passkey(
        seeded.id, seeded.id, _passkey()
    )

    with pytest.raises(ValueError):
        await passkey_service.update_sign_count(stored.id, 0, seeded.id)


async def test_passkey_update_sign_count_rejects_requester_mismatch(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
) -> None:
    """`update_sign_count` raises PermissionError on requester mismatch."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    stored = await passkey_service.register_passkey(
        seeded.id, seeded.id, _passkey()
    )

    with pytest.raises(PermissionError):
        await passkey_service.update_sign_count(
            stored.id, 5, "someone-else"
        )


async def test_passkey_update_sign_count_writes_new_value(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
) -> None:
    """`update_sign_count` writes the bumped counter via `repo.update_passkey_sign_count`."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    stored = await passkey_service.register_passkey(
        seeded.id, seeded.id, _passkey()
    )

    updated = await passkey_service.update_sign_count(stored.id, 5, seeded.id)

    assert updated.sign_count == 5


async def test_passkey_revoke_rejects_requester_mismatch(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
) -> None:
    """`revoke_passkey` raises PermissionError on requester mismatch."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    stored = await passkey_service.register_passkey(
        seeded.id, seeded.id, _passkey()
    )

    with pytest.raises(PermissionError):
        await passkey_service.revoke_passkey(stored.id, "someone-else")


async def test_passkey_revoke_stamps_revoked_at(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
) -> None:
    """`revoke_passkey` sets `revoked_at` on the row."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    stored = await passkey_service.register_passkey(
        seeded.id, seeded.id, _passkey()
    )
    assert stored.revoked_at is None

    revoked = await passkey_service.revoke_passkey(stored.id, seeded.id)

    assert revoked.revoked_at is not None


async def test_passkey_revoke_is_idempotent(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
) -> None:
    """A second `revoke_passkey` call leaves `revoked_at` unchanged."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    stored = await passkey_service.register_passkey(
        seeded.id, seeded.id, _passkey()
    )
    first = await passkey_service.revoke_passkey(stored.id, seeded.id)
    second = await passkey_service.revoke_passkey(stored.id, seeded.id)

    assert first.revoked_at == second.revoked_at
