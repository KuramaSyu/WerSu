"""Pin the UNDEFINED-as-skip wire rule for PostgresUserAuthRepoImpl.

The repo is a thin SQL mapper; the only contract worth pinning
without a real Postgres is the row it hands to the underlying
table (insert shape, update SET shape, third-party JSON shape).
"""

from __future__ import annotations

import datetime
from typing import Dict, List

from src.api.other.undefined import UNDEFINED
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.third_party import ThirdPartyEntity
from src.db.entities.user.user_auth import UserAuthEntity
from src.db.repos.user.user_auth_postgres import PostgresUserAuthRepoImpl
from src.grpc_mod.converter.postgres_row_converter import (
    PostgresRowConverter,
)
from tests.stubs.recording_table import RecordingTable


def _wire() -> tuple[PostgresUserAuthRepoImpl, RecordingTable, RecordingTable,
                     RecordingTable, RecordingTable]:
    """Build the repo with four fake tables; return them all for assertions."""
    user = RecordingTable("auth.user")
    password = RecordingTable("auth.password")
    passkey = RecordingTable("auth.passkey")
    third = RecordingTable("auth.third_party")
    # fixed now so timestamp-default fields stay inspectable
    converter = PostgresRowConverter(now=lambda: datetime.datetime(2026, 1, 1))
    repo = PostgresUserAuthRepoImpl(
        user_table=user,
        password_table=password,
        passkey_table=passkey,
        third_party_table=third,
        to_postgres_row=converter,
    )
    return repo, user, password, passkey, third


def _insert_calls(table: RecordingTable) -> List[Dict]:
    """Return the where dict of every insert call on table."""
    return [c.kwargs["where"] for c in table.calls_of("insert")]


def _update_calls(table: RecordingTable) -> List[Dict]:
    """Return one entry with set and where for every update."""
    return [
        {"set": c.kwargs["set"], "where": c.kwargs["where"]}
        for c in table.calls_of("update")
    ]


# ---------------------------------------------------------------------
# user INSERT
# ---------------------------------------------------------------------


async def test_user_insert_skips_undefined_fields() -> None:
    """UNDEFINED fields never reach table.insert; None is an explicit clear."""
    repo, user, _pw, _pk, _tp = _wire()

    await repo.insert(
        UserAuthEntity(
            id=UNDEFINED,
            avatar="pic.png",
            username=UNDEFINED,
            email=None,
            type="local",
        )
    )

    rows = _insert_calls(user)
    assert len(rows) == 1
    row = rows[0]
    assert all(v is not UNDEFINED for v in row.values())
    assert "username" not in row
    assert row["avatar"] == "pic.png"
    assert row["email"] is None
    assert row["type"] == "local"


async def test_user_insert_with_all_undefined_drops_every_column() -> None:
    """An all-UNDEFINED user collapses to an empty insert row."""
    repo, user, _pw, _pk, _tp = _wire()

    await repo.insert(
        UserAuthEntity(
            id=UNDEFINED,
            avatar=UNDEFINED,
            username=UNDEFINED,
            email=UNDEFINED,
            type=UNDEFINED,
        )
    )

    rows = _insert_calls(user)
    assert len(rows) == 1
    assert all(
        k not in {"username", "avatar", "email", "type"}
        for k in rows[0]
    )


# ---------------------------------------------------------------------
# user UPDATE
# ---------------------------------------------------------------------


async def test_user_update_skips_undefined_fields_in_set_clause() -> None:
    """update skips UNDEFINED and keeps None as an explicit clear."""
    repo, user, _pw, _pk, _tp = _wire()
    user.stored["u-1"] = {
        "id": "u-1",
        "avatar": "old.png",
        "username": "alice",
        "email": "alice@example.com",
        "type": "local",
    }

    await repo.update(
        UserAuthEntity(
            id="u-1",
            avatar=UNDEFINED,
            username="alice2",
            email=None,
            type=UNDEFINED,
        )
    )

    updates = _update_calls(user)
    assert len(updates) == 1
    set_clause = updates[0]["set"]
    assert all(v is not UNDEFINED for v in set_clause.values())
    assert "avatar" not in set_clause
    assert "type" not in set_clause
    assert set_clause["username"] == "alice2"
    assert set_clause["email"] is None


async def test_user_update_with_all_undefined_skips_the_update_entirely() -> None:
    """All-UNDEFINED patch is a no-op; table.update is not called."""
    repo, user, _pw, _pk, _tp = _wire()
    user.stored["u-1"] = {
        "id": "u-1",
        "avatar": "old.png",
        "username": "alice",
        "email": "alice@example.com",
        "type": "local",
    }

    await repo.update(
        UserAuthEntity(
            id="u-1",
            avatar=UNDEFINED,
            username=UNDEFINED,
            email=UNDEFINED,
            type=UNDEFINED,
        )
    )

    assert _update_calls(user) == []


# ---------------------------------------------------------------------
# passkey INSERT
# ---------------------------------------------------------------------


async def test_passkey_insert_skips_undefined_fields() -> None:
    """UNDEFINED last_used_at and revoked_at never reach table.insert."""
    repo, _u, _pw, passkey, _tp = _wire()

    await repo.insert_passkey(
        PasskeyEntity(
            user_id="u-1",
            credential_id=b"cred",
            public_key=b"pub",
            sign_count=0,
            transports=["usb"],
            aaguid=b"\x00" * 16,
            backup_eligible=False,
            backup_state=False,
            user_verified=False,
            friendly_name="yubikey",
            created_at=datetime.datetime(2026, 1, 1),
            last_used_at=UNDEFINED,
            revoked_at=UNDEFINED,
        )
    )

    rows = _insert_calls(passkey)
    assert len(rows) == 1
    row = rows[0]
    assert all(v is not UNDEFINED for v in row.values())
    assert "last_used_at" not in row
    assert "revoked_at" not in row
    assert row["user_id"] == "u-1"
    assert row["credential_id"] == b"cred"
    assert row["friendly_name"] == "yubikey"


# ---------------------------------------------------------------------
# third-party INSERT
# ---------------------------------------------------------------------


async def test_third_party_insert_skips_undefined_id_and_provider_user_id() -> None:
    """insert_third_party drops the UNDEFINED id; extra_fields becomes JSON."""
    repo, _u, _pw, _pk, third = _wire()

    await repo.insert_third_party(
        ThirdPartyEntity(
            id=UNDEFINED,
            user_id="u-1",
            provider="discord",
            provider_user_id="111222333",
            extra_fields={"discriminator": "0001"},
        )
    )

    rows = _insert_calls(third)
    assert len(rows) == 1
    row = rows[0]
    assert "id" not in row or row.get("id") is UNDEFINED
    assert row["user_id"] == "u-1"
    assert row["provider"] == "discord"
    assert row["provider_user_id"] == "111222333"
    assert isinstance(row["extra_fields"], str)
    assert "discriminator" in row["extra_fields"]


# ---------------------------------------------------------------------
# user UPDATE -- third_parties branch
# ---------------------------------------------------------------------


async def test_user_update_third_parties_branch_is_no_op_when_empty() -> None:
    """An empty third_parties list skips the third-party diff loop."""
    repo, user, _pw, _pk, third = _wire()
    user.stored["u-1"] = {
        "id": "u-1",
        "avatar": "old.png",
        "username": "alice",
        "email": "alice@example.com",
        "type": "local",
    }

    await repo.update(
        UserAuthEntity(
            id="u-1",
            username="alice2",
            third_parties=[],
        )
    )

    assert _update_calls(third) == []
    assert _insert_calls(third) == []
