"""In-memory :class:`PermissionRepoABC` fake for unit tests.

Grants ``view`` on a configurable set of note / directory ids and
denies everything else.  Suitable for tests that need to exercise
``has_permission(actor, "view", ObjectRef(...))`` without a real
SpiceDB backend.

This stub is intentionally separate from
:class:`tests.stubs.in_memory_permission_repo.InMemoryPermissionRepo`:
that one grants ``edit_permissions`` per editable note id, which is
the wrong shape for the statistics-service permission check.
"""

from __future__ import annotations

from typing import List, Optional

from src.api.repos.permission_repo import PermissionRepoABC
from src.api.other.relationship import ObjectRef, Relationship
from src.api.other.user_context import UserContextABC


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

    async def lookup(self, relationship: Relationship) -> List[str]:
        return []

    async def lookup_relationships(
        self, relationship: Relationship,
    ) -> List[Relationship]:
        return []

    async def list_relationships(self, resource: ObjectRef) -> List[Relationship]:
        return []

    async def check(self, relationship: Relationship) -> bool:
        return False

    async def get_permissions(
        self, user: UserContextABC, resource: ObjectRef,
    ) -> List[str]:
        return ["view"] if await self.has_permission(user, "view", resource) else []

    async def resolve_children(
        self,
        directory_id: str,
        *,
        max_depth: int = 10,
        exclusive: bool = True,
    ) -> "ResolvedChildren":
        from src.api.repos.permission_repo import ResolvedChildren

        # The view-only stub knows no relations.  Tests that need
        # tree resolution use :class:`InMemoryPermissionRepo`
        # instead; this stub is only here to keep the ABC
        # instantiable.
        return ResolvedChildren(sub_directory_ids=[], note_ids=[], attachment_ids=[])