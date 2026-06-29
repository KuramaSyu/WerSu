"""In-memory :class:`PermissionServiceABC` fake for unit tests.

Only :meth:`replace_relationships` is implemented because that is the
hook the sharing service uses to swap reader/writer relations; other
methods raise ``NotImplementedError`` so accidental use is loud.
"""

from __future__ import annotations

from typing import List, Tuple

from src.api.relationship import ObjectRef, Relationship
from src.api.user_context import UserContextABC
from src.services.permissions import PermissionServiceABC


class _FakePermissionService(PermissionServiceABC):
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


__all__ = ["_FakePermissionService"]