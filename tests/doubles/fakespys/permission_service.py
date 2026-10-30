from __future__ import annotations

from typing import List, Tuple
from src.api.other.relationship import ObjectRef, Relationship
from src.api.other.user_context import UserContextABC
from src.services.permissions import PermissionServiceABC


class _FakeSpyPermissionService(PermissionServiceABC):
    """Permission service fake that records replace_relationships calls."""

    def __init__(self) -> None:
        self.replace_calls: List[Tuple[ObjectRef, List[Relationship], UserContextABC]] = []

    async def list_relationships(self, resource, actor):
        raise NotImplementedError()

    async def create_relationship(self, relationship, actor):
        raise NotImplementedError()

    async def delete_relationship(self, relationship, actor):
        raise NotImplementedError()

    async def replace_relationships(self, resource, relationships, actor):
        self.replace_calls.append((resource, list(relationships), actor))
        return list(relationships)


__all__ = [
    '_FakeSpyPermissionService',
]
