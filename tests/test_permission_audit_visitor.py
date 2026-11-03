"""Visitor tests for ``PermissionAuditEntity`` conversions."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.db.entities.permission_audit import PermissionAuditEntity
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.converter.postgres_row_converter import PostgresRowConverter
from src.grpc_mod.proto.note_pb2 import (
    PermissionAuditAction,
    PermissionAuditEntry,
)
from tests.doubles import SpyVisitor


def _entity() -> PermissionAuditEntity:
    return PermissionAuditEntity(
        id="audit-1",
        at=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc),
        actor_id="actor-1",
        accessed_as="user",
        action="grant",
        resource_type="note",
        resource_id="note-1",
        relation="reader",
        subject_type="role",
        subject_id="role-1",
        subject_relation="member",
        metadata={"level": "read"},
    )


def test_permission_audit_entity_dispatches_to_visitor() -> None:
    visitor = SpyVisitor()
    entity = _entity()
    result = entity.convert(visitor)
    assert result is entity
    assert visitor.permission_audits == [entity]


def test_permission_audit_grpc_visitor() -> None:
    message = _entity().convert(ConvertToGrpcVisitor())
    assert isinstance(message, PermissionAuditEntry)
    assert message.id == "audit-1"
    assert message.actor_id == "actor-1"
    assert message.action == PermissionAuditAction.PERMISSION_AUDIT_ACTION_GRANT
    assert message.resource_type == "note"
    assert message.subject_type == "role"
    assert message.subject_relation == "member"
    assert json.loads(message.metadata_json) == {"level": "read"}


def test_permission_audit_postgres_visitor() -> None:
    row = _entity().convert(PostgresRowConverter())
    assert row["id"] == "audit-1"
    assert row["action"] == "grant"
    assert row["resource_type"] == "note"
    assert row["subject_relation"] == "member"
    assert row["at"] == datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    assert json.loads(row["metadata"]) == {"level": "read"}
    assert "metadata_json" not in row