"""Tests for the :class:`UserAuthEntity` accessors.

Covers the new ``discord_id`` / ``discriminator`` /
``third_party`` accessors on :class:`UserAuthEntity` and the
``extra_fields`` helpers on :class:`ThirdPartyEntity`.
"""

from __future__ import annotations

import json

from src.db.entities.user.third_party import ThirdPartyEntity
from src.db.entities.user.user_auth import UserAuthEntity


def test_third_party_get_extra_returns_default_when_missing() -> None:
    """`get_extra` returns the supplied default for absent keys."""
    tp = ThirdPartyEntity(provider="discord", provider_user_id="1")
    assert tp.get_extra("discriminator") is None
    assert tp.get_extra("discriminator", "0000") == "0000"


def test_third_party_set_extra_then_get_extra_round_trips() -> None:
    """`set_extra` + `get_extra` round-trip."""
    tp = ThirdPartyEntity(provider="discord", provider_user_id="1")
    tp.set_extra("discriminator", "0001")
    assert tp.get_extra("discriminator") == "0001"


def test_third_party_serialised_extras_returns_none_when_empty() -> None:
    """`serialised_extras` is `None` when no extras are set."""
    tp = ThirdPartyEntity(provider="discord", provider_user_id="1")
    assert tp.serialised_extras is None


def test_third_party_serialised_extras_json_encodes_dict() -> None:
    """`serialised_extras` returns a JSON string when extras exist."""
    tp = ThirdPartyEntity(provider="discord", provider_user_id="1")
    tp.set_extra("discriminator", "0001")
    encoded = tp.serialised_extras
    assert encoded is not None
    assert json.loads(encoded) == {"discriminator": "0001"}


def test_user_auth_third_party_returns_linked_provider() -> None:
    """`third_party(provider)` returns the matching :class:`ThirdPartyEntity`."""
    user = UserAuthEntity(
        username="bob",
        email="bob@example.com",
        third_parties=[
            ThirdPartyEntity(provider="discord", provider_user_id="123"),
            ThirdPartyEntity(provider="google", provider_user_id="abc"),
        ],
    )

    assert user.third_party("discord") is not None
    assert user.third_party("discord").provider_user_id == "123"
    assert user.third_party("google").provider_user_id == "abc"


def test_user_auth_third_party_returns_none_for_unknown_provider() -> None:
    """`third_party(...)` returns ``None`` for unlinked providers."""
    user = UserAuthEntity(username="alice", email="a@b.c")
    assert user.third_party("discord") is None


def test_user_auth_discord_id_returns_linked_id_as_int() -> None:
    """`discord_id()` parses the linked provider_user_id as int."""
    user = UserAuthEntity(
        username="bob",
        email="b@b.c",
        third_parties=[
            ThirdPartyEntity(provider="discord", provider_user_id="987654321"),
        ],
    )
    assert user.discord_id() == 987654321


def test_user_auth_discord_id_returns_none_when_not_linked() -> None:
    """`discord_id()` returns ``None`` when no Discord row is linked."""
    user = UserAuthEntity(username="alice", email="a@b.c")
    assert user.discord_id() is None


def test_user_auth_discriminator_returns_discord_extra() -> None:
    """`discriminator()` reads the ``"discriminator"`` extra off the Discord row."""
    tp = ThirdPartyEntity(provider="discord", provider_user_id="987654321")
    tp.set_extra("discriminator", "0001")
    user = UserAuthEntity(
        username="bob",
        email="b@b.c",
        third_parties=[tp],
    )

    assert user.discriminator() == "0001"


def test_user_auth_discriminator_returns_none_when_no_discord() -> None:
    """`discriminator()` is ``None`` when no Discord row is linked."""
    user = UserAuthEntity(
        username="alice",
        email="a@b.c",
        third_parties=[
            ThirdPartyEntity(provider="google", provider_user_id="abc"),
        ],
    )
    assert user.discriminator() is None


def test_user_auth_discriminator_returns_none_when_extra_missing() -> None:
    """`discriminator()` is ``None`` when Discord row has no extra."""
    user = UserAuthEntity(
        username="alice",
        email="a@b.c",
        third_parties=[
            ThirdPartyEntity(provider="discord", provider_user_id="123"),
        ],
    )
    assert user.discriminator() is None
