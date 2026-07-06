"""In-memory :class:`PermissionRepoABC` fake for unit tests.

Grants ``view`` on a configurable set of note / directory ids and
denies everything else.  Suitable for tests that need to exercise
``has_permission(actor, "view", ObjectRef(...))`` without a real
SpiceDB backend.

This stub is intentionally separate from
``tests.stubs.permission_repo._FakePermissionRepo``: that one grants
``edit_permissions`` per editable note id, which is the wrong shape
for the statistics-service permission check.
"""

from __future__ import annotations

from typing import List, Optional

from src.api.permission_repo import PermissionRepoABC
from src.api.relationship import ObjectRef, Relationship
from src.api.user_context import UserContextABC


class _FakeViewPermissionRepo(PermissionRepoABC):
    """Permission fake that grants ``view`` for selected note / directory ids.

    Args:
        viewable_note_ids: note ids the caller may view.
        viewable_directory_ids: directory ids the caller may view.
    """

    def __init__(
        self,
        viewable_note_ids: Optional[List[str]] = None,
        viewable_directory_ids: Optional[List[str]] = None,
    ) -> None:
        self._notes = set(viewable_note_ids or [])
        self._dirs = set(viewable_directory_ids or [])

    async def has_permission(
        self,
        user: UserContextABC,
        permission: str,
        resource: ObjectRef,
    ) -> bool:
        if permission != "view":
            return False
        if resource.object_type == "note":
            return resource.object_id in self._notes
        if resource.object_type == "directory":
            return resource.object_id in self._dirs
        return False

    # ---- unimplemented stubs below; tests don't exercise them ----

    async def insert(self, relationships: List[Relationship]) -> List[Relationship]:
        return list(relationships)

    async def delete(self, relationship: Relationship) -> Relationship:
        return relationship

    async def lookup(self, relationship: Relationship) -> List[ObjectRef]:
        return []

    async def lookup_relationships(
        self, relationship: Relationship,
    ) -> List[Relationship]:
        return []

    async def lookup_notes(
        self, user: UserContextABC, permission: str,
    ) -> List[ObjectRef]:
        return [ObjectRef(object_type="note", object_id=n) for n in self._notes]

    async def list_relationships(self, resource: ObjectRef) -> List[Relationship]:
        return []

    async def check(self, relationship: Relationship) -> bool:
        return False

    async def get_permissions(
        self, user: UserContextABC, resource: ObjectRef,
    ) -> List[str]:
        return ["view"] if await self.has_permission(user, "view", resource) else []