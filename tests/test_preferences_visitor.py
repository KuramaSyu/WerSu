"""Unit tests for the PreferencesEntity visitor dispatch.

Each visitor (PostgresRowConverter, ConvertToGrpcVisitor) is
exercised by routing an entity through visit(); the assertions
cover the row-dict projection for Postgres and the dict-shaped
projection used by the gRPC layer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.db.entities.preferences import PreferencesEntity
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.converter.postgres_row_converter import PostgresRowConverter


_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entity(scope: str = "user", **owner) -> PreferencesEntity:
    """Build a PreferencesEntity with the given scope + owner id."""
    payload = {"theme": "dark", "page_size": 25}
    kwargs = dict(owner)
    return PreferencesEntity(
        scope=scope, payload=payload, updated_at=_TS, **kwargs
    )


# ---------------------------------------------------------------------------
# Entity itself
# ---------------------------------------------------------------------------


def test_entity_dispatches_to_visit_preferences() -> None:
    """PreferencesEntity.visit routes to the visitor's visit_preferences."""
    captured = {}

    class _Recorder:
        def visit_preferences(self, entity):
            captured["entity"] = entity
            return "ok"

    entity = _entity("user", user_id="u1")
    result = entity.visit(_Recorder())

    assert result == "ok"
    assert captured["entity"] is entity


def test_owner_id_returns_the_scoped_owner() -> None:
    """owner_id() picks the column that matches the scope."""
    assert _entity("user", user_id="u1").owner_id() == "u1"
    assert _entity("shelf", shelf_id="s-1").owner_id() == "s-1"
    assert _entity("role", role_id="r-1").owner_id() == "r-1"


def test_owner_id_rejects_multiply_shaped_entity() -> None:
    """An entity with two owners set must raise."""
    bad = PreferencesEntity(
        scope="user",
        user_id="u1",
        shelf_id="s-1",
        payload={},
        updated_at=_TS,
    )
    with pytest.raises(ValueError, match="exactly one of"):
        bad.owner_id()


# ---------------------------------------------------------------------------
# PostgresRowConverter.visit_preferences
# ---------------------------------------------------------------------------


def test_postgres_visitor_drops_scope_and_json_serialises_payload() -> None:
    """Entity -> row dict via the visitor."""
    converter = PostgresRowConverter()
    row = _entity("user", user_id="u1").visit(converter)

    assert "scope" not in row
    assert row["user_id"] == "u1"
    assert json.loads(row["payload"]) == {"theme": "dark", "page_size": 25}


@pytest.mark.parametrize(
    ("scope", "owner_column"),
    [
        ("user", "user_id"),
        ("shelf", "shelf_id"),
        ("role", "role_id"),
    ],
)
def test_postgres_visitor_projects_only_the_scoped_owner(
    scope: str, owner_column: str
) -> None:
    """Only the column matching the scope is populated; others are absent."""
    converter = PostgresRowConverter()
    entity = _entity(scope, **{owner_column: "owner-1"})
    row = entity.visit(converter)

    assert row[owner_column] == "owner-1"
    for other in ("user_id", "shelf_id", "role_id"):
        if other == owner_column:
            continue
        assert other not in row


# ---------------------------------------------------------------------------
# ConvertToGrpcVisitor.visit_preferences
# ---------------------------------------------------------------------------


def test_grpc_visitor_serialises_payload_as_compact_json() -> None:
    """The gRPC visitor renders the payload as a JSON object string."""
    visitor = ConvertToGrpcVisitor()
    result = _entity("shelf", shelf_id="s-9").visit(visitor)
    assert result.preferences_json == '{"theme":"dark","page_size":25}'
    assert result.key == ""


def test_grpc_visitor_carries_key_through() -> None:
    """A keyed entity surfaces its key on the response."""
    visitor = ConvertToGrpcVisitor()
    entity = _entity("role", role_id="r-2")
    entity.key = "ui"
    result = entity.visit(visitor)
    assert result.key == "ui"


def test_grpc_visitor_defaults_missing_payload_to_empty_object() -> None:
    """No payload serialises to {} rather than erroring."""
    visitor = ConvertToGrpcVisitor()
    entity = PreferencesEntity(scope="user", user_id="u-3")
    result = entity.visit(visitor)
    assert result.preferences_json == "{}"
    assert result.key == ""


def test_grpc_visitor_accepts_preencoded_json_string() -> None:
    """A payload that is already a JSON string passes through verbatim."""
    visitor = ConvertToGrpcVisitor()
    entity = PreferencesEntity(
        scope="user", user_id="u-1", payload='{"a":1}'
    )
    result = entity.visit(visitor)
    assert result.preferences_json == '{"a":1}'
