"""Entity and filter for the dedicated ``permission_audit`` trail.

One row is written per successful direct grant/revoke of a SpiceDB tuple.
``replace_relationships`` writes one row per added and one per removed
relationship, so the trail is a faithful append-only mirror of the writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping

from src.api.other.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
)
from src.api.other.visitor import AcceptsVisitor, EntityVisitor

PermissionAuditAction = Literal["grant", "revoke"]


@dataclass
class PermissionAuditEntity(AcceptsVisitor):
    """A single ``public.permission_audit`` row."""

    id: UndefinedOr[str] = UNDEFINED
    at: UndefinedNoneOr[datetime] = UNDEFINED
    actor_id: UndefinedNoneOr[str] = UNDEFINED
    accessed_as: UndefinedOr[Literal["user", "system"]] = UNDEFINED

    action: UndefinedOr[PermissionAuditAction] = UNDEFINED

    resource_type: UndefinedOr[str] = UNDEFINED
    resource_id: UndefinedOr[str] = UNDEFINED
    relation: UndefinedOr[str] = UNDEFINED

    subject_type: UndefinedOr[str] = UNDEFINED
    subject_id: UndefinedOr[str] = UNDEFINED
    subject_relation: UndefinedNoneOr[str] = UNDEFINED

    metadata: UndefinedOr[Mapping[str, object]] = UNDEFINED

    def visit(self, visitor: EntityVisitor) -> Any:
        """Dispatch this audit row to ``visitor.visit_permission_audit``."""
        return visitor.visit_permission_audit(self)


@dataclass
class FilterPermissionAudit:
    """Filter for reading the trail (used by a future audit UI)."""

    resource_type: UndefinedOr[str] = UNDEFINED
    resource_id: UndefinedOr[str] = UNDEFINED
    actor_id: UndefinedNoneOr[str] = UNDEFINED
    action: UndefinedOr[PermissionAuditAction] = UNDEFINED
    since: UndefinedNoneOr[datetime] = UNDEFINED
    limit: UndefinedOr[int] = UNDEFINED
    offset: UndefinedOr[int] = UNDEFINED


__all__ = [
    "FilterPermissionAudit",
    "PermissionAuditAction",
    "PermissionAuditEntity",
]