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

from src.api.other.undefined import UNDEFINED
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
    with pytest.raises(ValueError):
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


def test_grpc_visitor_returns_owner_column_value() -> None:
    """The gRPC visitor surfaces the same owner column the entity carries."""
    visitor = ConvertToGrpcVisitor()
    result = _entity("shelf", shelf_id="s-9").visit(visitor)
    assert result["shelf_id"] == "s-9"
    assert "user_id" not in result
    assert "role_id" not in result


def test_grpc_visitor_carries_payload_through_unchanged() -> None:
    """The gRPC layer hands the payload dict to the caller verbatim."""
    visitor = ConvertToGrpcVisitor()
    entity = _entity("role", role_id="r-2")
    result = entity.visit(visitor)
    assert result["payload"] == {"theme": "dark", "page_size": 25}


def test_grpc_visitor_omits_undefined_id() -> None:
    """An entity without an id should produce an UNDEFINED id marker."""
    visitor = ConvertToGrpcVisitor()
    entity = _entity("user", user_id="u-3")
    result = entity.visit(visitor)
    assert result["id"] is UNDEFINED
