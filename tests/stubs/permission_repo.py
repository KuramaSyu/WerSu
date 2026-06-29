"""In-memory permission repo fake for unit tests.

Grants ``edit_permissions`` for a configurable set of note IDs and
stores relationships inline so the permission-swap path can be
exercised without a real SpiceDB client.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from src.api.relationship import ObjectRef, Relationship
from src.api.user_context import UserContextABC


class _FakePermissionRepo:
    """Permission fake that grants edit access for selected note IDs."""

    def __init__(
        self,
        editable_note_ids: set[str],
        permissions_by_access_user: Optional[dict[Tuple[str, str], List[str]]] = None,
        stored_relationships: Optional[List[Relationship]] = None,
    ) -> None:
        self.editable_note_ids = editable_note_ids
        self.checked_note_ids: List[str] = []
        self._relationships: List[Relationship] = list(stored_relationships or [])
        # (note_id, access_as) -> effective permission strings
        self._permissions_by_access_user: dict[Tuple[str, str], List[str]] = (
            permissions_by_access_user or {}
        )

    async def has_permission(self, user, permission: str, resource) -> bool:
        self.checked_note_ids.append(str(resource.object_id))
        return permission == "edit_permissions" and resource.object_id in self.editable_note_ids

    async def insert(self, relationships: List[Relationship]) -> List[Relationship]:
        for rel in relationships:
            self._relationships.append(rel)
        return list(relationships)

    async def delete(self, relationship: Relationship) -> Relationship:
        self._relationships = [
            rel for rel in self._relationships
            if not (
                str(rel.resource.object_type) == str(relationship.resource.object_type)
                and str(rel.resource.object_id) == str(relationship.resource.object_id)
                and str(rel.relation) == str(relationship.relation)
                and str(rel.subject.object_type) == str(relationship.subject.object_type)
                and str(rel.subject.object_id) == str(relationship.subject.object_id)
            )
        ]
        return relationship

    async def list_relationships(self, resource: ObjectRef) -> List[Relationship]:
        return [
            rel for rel in self._relationships
            if str(rel.resource.object_type) == str(resource.object_type)
            and str(rel.resource.object_id) == str(resource.object_id)
        ]

    async def get_permissions(self, user, resource) -> List[str]:
        return list(
            self._permissions_by_access_user.get(
                (str(resource.object_id), str(user.user_id)),
                [],
            )
        )


__all__ = ["_FakePermissionRepo"]