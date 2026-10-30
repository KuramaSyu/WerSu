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
* :meth:`UserPasskeyAuthServiceImpl.update_sign_count` increments
  the stored counter by one and rejects a requester that does not own
  the passkey.
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

from tests.doubles.spys.user_auth_repo_impl import RecordingUserAuthRepo, _Call, _is_undefined


# ---------------------------------------------------------------------
# Recording fake
# ---------------------------------------------------------------------








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


def _passkey(
    user_id: str = "placeholder", sign_count: int = 0
) -> PasskeyEntity:
    return PasskeyEntity(
        id=UNDEFINED,
        user_id=user_id,
        credential_id=b"cred-id",
        public_key=b"public-key",
        sign_count=sign_count,
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
            stored.id, "someone-else"
        )


async def test_passkey_update_sign_count_increments_by_one(
    auth_service: UserAuthServiceABC,
    passkey_service: UserPasskeyAuthServiceABC,
) -> None:
    """`update_sign_count` bumps the stored counter by one server-side."""
    seeded = await auth_service.create_user(
        UserAuthEntity(id=UNDEFINED, username="alice", email="a@b.c")
    )
    stored = await passkey_service.register_passkey(
        seeded.id, seeded.id, _passkey(sign_count=4)
    )

    updated = await passkey_service.update_sign_count(stored.id, seeded.id)

    assert updated.sign_count == stored.sign_count + 1


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
