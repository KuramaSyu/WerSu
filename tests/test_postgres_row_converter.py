"""Unit tests for :class:`PostgresRowConverter`.

Covers the visitor contract end-to-end:

* dispatch through ``entity.visit(converter)`` returns a dict;
* ``UNDEFINED`` fields are dropped;
* JSON-serialised columns (``metadata``, ``condition``,
  ``action_context``, ``extra_fields``) end up as JSON strings;
* the injected ``now()`` callable fills missing timestamp fields;
* per-entity quirks like ``note_id`` -> ``id`` rename and
  ``checksum`` -> ``sha256`` on :class:`Attachment` round-trip.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import List

import pytest

from src.api.other.undefined import UNDEFINED
from src.api.services.note_service import NoteResponse
from src.db.entities.activity import ActivityEntity, ActivityScore
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.sharing import NoteShareEntity
from src.db.entities.rule import RuleEntity
from src.db.entities.shelf import ShelfEntity
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.password import PasswordEntity
from src.db.entities.user.role import (
    RoleEntity,
    UserRoleMembershipEntity,
)
from src.db.entities.user.third_party import ThirdPartyEntity
from src.db.entities.user.user import UserEntity
from src.db.entities.user.user_auth import UserAuthEntity
from src.db.repos.attachments.attachments import Attachment
from src.grpc_mod.converter.postgres_row_converter import (
    JSON_FIELDS,
    TIMESTAMP_FIELDS,
    PostgresRowConverter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frozen_now() -> _dt.datetime:
    """A deterministic timestamp used by the ``now`` injection tests."""
    return _dt.datetime(2026, 1, 1, 12, 0, 0)


def _converter_with_frozen_now() -> PostgresRowConverter:
    return PostgresRowConverter(now=_frozen_now)


# ---------------------------------------------------------------------------
# now() injection
# ---------------------------------------------------------------------------


def test_now_default_uses_datetime_now(monkeypatch) -> None:
    """`PostgresRowConverter()` with no `now` arg uses `datetime.now`."""
    converter = PostgresRowConverter()
    assert converter._now is not None
    sample = converter._now()
    assert isinstance(sample, _dt.datetime)


def test_visit_note_fills_undefined_updated_at_with_now() -> None:
    """`updated_at` is filled with the injected `now()` when UNDEFINED."""
    converter = _converter_with_frozen_now()
    entity = NoteEntity(
        note_id="n1",
        title="t",
        content="c",
        author_id="a1",
    )

    row = converter.visit_note(entity)

    assert row["updated_at"] == _frozen_now()


def test_visit_note_preserves_explicit_timestamp() -> None:
    """A concrete `updated_at` survives intact even when `now` is set."""
    converter = _converter_with_frozen_now()
    explicit = _dt.datetime(2030, 5, 5, 10, 0, 0)
    entity = NoteEntity(
        note_id="n1",
        title="t",
        content="c",
        author_id="a1",
        updated_at=explicit,
    )

    row = converter.visit_note(entity)

    assert row["updated_at"] == explicit


def test_visit_activity_fills_at_with_now_when_undefined() -> None:
    """`at` is filled with the injected `now()` when UNDEFINED."""
    converter = _converter_with_frozen_now()
    entity = ActivityEntity(action="note_viewed", note_id="n1")

    row = converter.visit_activity(entity)

    assert row["at"] == _frozen_now()


def test_visit_third_party_fills_created_at_with_now_when_undefined() -> None:
    """`created_at` is filled with the injected `now()` when UNDEFINED."""
    converter = _converter_with_frozen_now()
    entity = ThirdPartyEntity(
        user_id="u1",
        provider="discord",
        provider_user_id="1234",
    )

    row = converter.visit_third_party(entity)

    assert row["created_at"] == _frozen_now()


def test_visit_password_fills_created_at_with_now_when_undefined() -> None:
    """`PasswordEntity.created_at` is filled with the injected `now()`."""
    converter = _converter_with_frozen_now()
    entity = PasswordEntity(user_id="u1", password_hash="hash")

    row = converter.visit_password(entity)

    assert row["created_at"] == _frozen_now()


# ---------------------------------------------------------------------------
# note / note_minimal
# ---------------------------------------------------------------------------


def test_visit_note_renames_note_id_to_id() -> None:
    """`NoteEntity.note_id` is renamed to the row column `id`."""
    converter = PostgresRowConverter()
    entity = NoteEntity(
        note_id="n1",
        title="t",
        content="c",
        author_id="a1",
    )

    row = converter.visit_note(entity)

    assert "note_id" not in row
    assert row["id"] == "n1"


def test_visit_note_drops_note_id_when_undefined() -> None:
    """`id` is omitted from the row when the entity left `note_id` UNDEFINED."""
    converter = PostgresRowConverter()
    entity = NoteEntity(
        title="t",
        content="c",
        author_id="a1",
    )

    row = converter.visit_note(entity)

    assert "id" not in row


def test_visit_note_drops_undefined_fields() -> None:
    """UNDEFINED fields never end up in the row."""
    converter = PostgresRowConverter()
    entity = NoteEntity(
        note_id="n1",
        title="t",
        content="c",
        author_id="a1",
    )

    row = converter.visit_note(entity)

    assert "directory_ids" not in row
    assert "tag_ids" not in row
    assert "attachment_ids" not in row
    assert "shelf_ids" not in row
    assert "embeddings" not in row
    assert "permissions" not in row


def test_visit_note_preserves_defined_lists() -> None:
    """Defined list fields pass through to the row untouched."""
    converter = PostgresRowConverter()
    entity = NoteEntity(
        note_id="n1",
        title="t",
        content="c",
        author_id="a1",
        directory_ids=["d1", "d2"],
        tag_ids=["t1"],
    )

    row = converter.visit_note(entity)

    assert row["directory_ids"] == ["d1", "d2"]
    assert row["tag_ids"] == ["t1"]


def test_visit_note_response_dispatches_to_inner_note() -> None:
    """`visit_note_response` falls through to the inner note's row."""
    converter = PostgresRowConverter()
    note = NoteEntity(
        note_id="n1", title="t", content="c", author_id="a1",
    )

    row = converter.visit_note_response(NoteResponse(note=note))

    assert row["id"] == "n1"


def test_visit_note_response_with_none_note_returns_empty_dict() -> None:
    """A `None` note produces an empty dict (the inner entity is absent)."""
    converter = PostgresRowConverter()
    assert converter.visit_note_response(NoteResponse(note=None)) == {}


def test_visit_note_minimal_strips_heavy_fields() -> None:
    """`visit_note_minimal` drops content + the m2m lists."""
    converter = PostgresRowConverter()
    entity = NoteEntity(
        note_id="n1",
        title="t",
        content="c",
        author_id="a1",
        directory_ids=["d1"],
        tag_ids=["t1"],
        attachment_ids=["a1"],
        shelf_ids=["s1"],
    )

    row = converter.visit_note_minimal(entity)

    assert "content" not in row
    assert "directory_ids" not in row
    assert "tag_ids" not in row
    assert "attachment_ids" not in row
    assert "shelf_ids" not in row
    assert "embeddings" not in row
    assert "permissions" not in row
    assert row["title"] == "t"
    assert row["author_id"] == "a1"


# ---------------------------------------------------------------------------
# directory / shelf
# ---------------------------------------------------------------------------


def test_visit_directory_keeps_scalar_fields() -> None:
    """`visit_directory` keeps slug, display_name etc. as-is."""
    converter = PostgresRowConverter()
    entity = DirectoryEntity(
        id="d1",
        slug="docs",
        display_name="Docs",
        description="d",
        image_url="u",
    )

    row = converter.visit_directory(entity)

    assert row == {
        "id": "d1",
        "slug": "docs",
        "display_name": "Docs",
        "description": "d",
        "image_url": "u",
    }


def test_visit_shelf_drops_undefined_metadata() -> None:
    """`visit_shelf` only includes fields the entity actually set."""
    converter = PostgresRowConverter()
    entity = ShelfEntity(id="s1", slug="mine")

    row = converter.visit_shelf(entity)

    assert row == {"id": "s1", "slug": "mine"}


# ---------------------------------------------------------------------------
# user / user_auth
# ---------------------------------------------------------------------------


def test_visit_user_keeps_discord_fields_for_legacy_repo() -> None:
    """`UserEntity` keeps `discord_id` and `discriminator` -- the legacy
    ``UserPostgresRepo`` strips them itself before the INSERT.
    """
    converter = PostgresRowConverter()
    entity = UserEntity(
        id="u1",
        discord_id=42,
        avatar="a",
        username="alice",
        discriminator="0001",
        email="a@b.c",
    )

    row = converter.visit_user(entity)

    assert row["discord_id"] == 42
    assert row["discriminator"] == "0001"


def test_visit_user_auth_drops_third_parties() -> None:
    """`third_parties` lives on its own table; it is dropped from the row."""
    converter = PostgresRowConverter()
    tp = ThirdPartyEntity(
        user_id="u1",
        provider="discord",
        provider_user_id="1234",
    )
    entity = UserAuthEntity(
        id="u1",
        avatar="a",
        username="alice",
        email="a@b.c",
        third_parties=[tp],
    )

    row = converter.visit_user_auth(entity)

    assert "third_parties" not in row
    assert row["id"] == "u1"
    assert row["email"] == "a@b.c"


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------


def test_visit_activity_serialises_metadata_to_json_string() -> None:
    """`metadata` is serialised via `json.dumps` so asyncpg can hand it
    straight to a JSONB column."""
    converter = PostgresRowConverter()
    entity = ActivityEntity(
        action="note_viewed",
        note_id="n1",
        metadata={"added": ["t1"], "removed": []},
    )

    row = converter.visit_activity(entity)

    assert isinstance(row["metadata"], str)
    assert json.loads(row["metadata"]) == {"added": ["t1"], "removed": []}


def test_visit_activity_serialises_undefined_metadata_as_empty_object() -> None:
    """An UNDEFINED / None `metadata` lands as the JSON object `{}`."""
    converter = PostgresRowConverter()
    entity = ActivityEntity(action="note_viewed", note_id="n1")

    row = converter.visit_activity(entity)

    assert row["metadata"] == "{}"


def test_visit_third_party_serialises_extra_fields_to_json_string() -> None:
    """`extra_fields` becomes a JSON string so the JSONB column accepts it."""
    converter = PostgresRowConverter()
    entity = ThirdPartyEntity(
        user_id="u1",
        provider="discord",
        provider_user_id="1234",
        extra_fields={"discriminator": "0001"},
    )

    row = converter.visit_third_party(entity)

    assert isinstance(row["extra_fields"], str)
    assert json.loads(row["extra_fields"]) == {"discriminator": "0001"}


def test_visit_third_party_empty_extra_fields_serialise_to_empty_object() -> None:
    """An empty `extra_fields` dict lands as the JSON object `{}`."""
    converter = PostgresRowConverter()
    entity = ThirdPartyEntity(
        user_id="u1",
        provider="discord",
        provider_user_id="1234",
    )

    row = converter.visit_third_party(entity)

    assert row["extra_fields"] == "{}"


def test_visit_rule_serialises_jsonb_columns() -> None:
    """Both JSONB columns on `RuleEntity` are JSON-serialised."""
    converter = PostgresRowConverter()
    entity = RuleEntity(
        event_type="note_updated",
        condition={"kind": "always"},
        action_type="noop",
        action_context={},
        enabled=True,
        creator_id="u1",
    )

    row = converter.visit_rule(entity)

    assert isinstance(row["condition"], str)
    assert json.loads(row["condition"]) == {"kind": "always"}
    assert isinstance(row["action_context"], str)
    assert row["action_context"] == "{}"


# ---------------------------------------------------------------------------
# attachment
# ---------------------------------------------------------------------------


def test_visit_attachment_drops_content_and_renames_checksum_to_sha256() -> None:
    """`content` (bytes) is dropped; `checksum` becomes `sha256`."""
    converter = PostgresRowConverter()
    entity = Attachment(
        key="k1",
        filename="hello.txt",
        filepath="uploads/hello.txt",
        content_type="text/plain",
        size=5,
        content=b"hello",
        checksum="abc",
    )

    row = converter.visit_attachment(entity)

    assert "content" not in row
    assert "checksum" not in row
    assert row["sha256"] == "abc"
    assert row["size"] == 5
    assert row["content_type"] == "text/plain"


def test_visit_attachment_computes_sha256_from_content_when_checksum_missing() -> None:
    """sha256 falls back to hashlib.sha256(content) when caller did not set checksum."""
    import hashlib

    converter = PostgresRowConverter()
    entity = Attachment(
        key="k1",
        filename="hello.txt",
        filepath="uploads/hello.txt",
        content_type="text/plain",
        size=5,
        content=b"hello",
    )

    row = converter.visit_attachment(entity)

    assert "checksum" not in row
    assert row["sha256"] == hashlib.sha256(b"hello").hexdigest()


def test_visit_attachment_metadata_uses_same_logic_as_visit_attachment() -> None:
    """`visit_attachment_metadata` is currently an alias of `visit_attachment`."""
    converter = _converter_with_frozen_now()
    entity = Attachment(
        key="k1",
        filename="x",
        filepath="x",
        content_type="text/plain",
        size=1,
        content=b"x",
        checksum="abc",
    )

    assert converter.visit_attachment_metadata(entity) == converter.visit_attachment(entity)


# ---------------------------------------------------------------------------
# activity score / role / membership / share / passkey
# ---------------------------------------------------------------------------


def test_visit_activity_score_coerces_score_to_float() -> None:
    """`score` is always a plain float on the row."""
    converter = PostgresRowConverter()
    score = ActivityScore(note_id="n1", score=42)

    row = converter.visit_activity_score(score)

    assert row["score"] == 42.0
    assert isinstance(row["score"], float)


def test_visit_role_keeps_all_scalar_fields() -> None:
    """`visit_role` keeps id, name, description, created_at."""
    converter = _converter_with_frozen_now()
    entity = RoleEntity(
        id="r1",
        name="admin",
        description="admins",
    )

    row = converter.visit_role(entity)

    assert row == {
        "id": "r1",
        "name": "admin",
        "description": "admins",
        "created_at": _frozen_now(),
    }


def test_visit_user_role_membership_strips_undefined_granted_at() -> None:
    """UNDEFINED `granted_at` is dropped -- the raw SpiceDB tuple has no timestamp."""
    converter = PostgresRowConverter()
    entity = UserRoleMembershipEntity(user_id="u1", role_id="r1")

    row = converter.visit_user_role_membership(entity)

    assert "granted_at" not in row
    assert row == {"user_id": "u1", "role_id": "r1"}


def test_visit_user_action_fills_execute_at_with_now() -> None:
    """`execute_at` is filled with the injected `now()` when UNDEFINED."""
    from src.db.entities.user.user_action import UserActionEntity
    converter = _converter_with_frozen_now()
    entity = UserActionEntity(
        user_id="u1", action="disable", execute_at=UNDEFINED,
    )

    row = converter.visit_user_action(entity)

    assert row["execute_at"] == _frozen_now()


def test_visit_note_version_snapshot_fills_created_at_with_now() -> None:
    """`created_at` is filled with the injected `now()` when UNDEFINED."""
    from src.db.entities.note.versioning import NoteVersionSnapshotEntity
    converter = _converter_with_frozen_now()
    entity = NoteVersionSnapshotEntity(
        note_id="n1", version_index=1, author_id="u1", title="t", content="c",
    )

    row = converter.visit_note_version_snapshot(entity)

    assert row["created_at"] == _frozen_now()


def test_visit_note_version_delta_fills_created_at_with_now() -> None:
    """`created_at` is filled with the injected `now()` when UNDEFINED."""
    from src.db.entities.note.versioning import NoteVersionDeltaEntity
    converter = _converter_with_frozen_now()
    entity = NoteVersionDeltaEntity(
        note_id="n1", snapshot_id="s1", version_index=1, author_id="u1",
    )

    row = converter.visit_note_version_delta(entity)

    assert row["created_at"] == _frozen_now()


def test_visit_note_share_keeps_undefined_keys() -> None:
    """`UndefinedOr` fields are dropped from the share row."""
    converter = PostgresRowConverter()
    entity = NoteShareEntity(
        id="s1",
        note_id="n1",
        created_at=_dt.datetime(2026, 1, 1),
        created_by="alice",
        access_as="u1",
        permission="read",
    )

    row = converter.visit_note_share(entity)

    assert "description" not in row
    assert "online_since" not in row
    assert "online_until" not in row
    assert row["permission"] == "read"


def test_visit_passkey_keeps_bytes_fields() -> None:
    """`credential_id` / `public_key` / `aaguid` (bytes) pass through unchanged."""
    converter = PostgresRowConverter()
    entity = PasskeyEntity(
        id="p1",
        user_id="u1",
        credential_id=b"cid",
        public_key=b"pk",
        sign_count=0,
        transports=["usb"],
        aaguid=b"aag",
        backup_eligible=True,
        backup_state=False,
        user_verified=True,
        friendly_name="key",
    )

    row = converter.visit_passkey(entity)

    assert row["credential_id"] == b"cid"
    assert row["public_key"] == b"pk"
    assert row["aaguid"] == b"aag"
    assert row["transports"] == ["usb"]


# ---------------------------------------------------------------------------
# Convert helper / dispatch
# ---------------------------------------------------------------------------


def test_convert_helper_routes_via_entity_visit() -> None:
    """`PostgresRowConverter.convert(entity)` calls `entity.visit(self)`."""
    converter = PostgresRowConverter()
    entity = NoteEntity(
        note_id="n1", title="t", content="c", author_id="a1",
    )

    row = converter.convert(entity)

    assert row["id"] == "n1"


def test_convert_helper_falls_back_to_dataclass_field_walk() -> None:
    """A bare dataclass (no `visit`) is also projectable via `convert`."""
    from dataclasses import dataclass
    converter = PostgresRowConverter()

    @dataclass
    class _Bare:
        a: int = 1
        b: str = "x"

    assert converter.convert(_Bare()) == {"a": 1, "b": "x"}


def test_json_fields_constant_includes_metadata() -> None:
    """The `JSON_FIELDS` constant advertises the four JSONB columns."""
    assert "metadata" in JSON_FIELDS
    assert "condition" in JSON_FIELDS
    assert "action_context" in JSON_FIELDS
    assert "extra_fields" in JSON_FIELDS


def test_timestamp_fields_constant_includes_common_columns() -> None:
    """The `TIMESTAMP_FIELDS` constant advertises the common time columns."""
    assert "created_at" in TIMESTAMP_FIELDS
    assert "updated_at" in TIMESTAMP_FIELDS
    assert "at" in TIMESTAMP_FIELDS
