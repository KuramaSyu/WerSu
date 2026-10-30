from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from src.api.other.user_context import UserContextABC
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
    DirectoryParentType,
)
from src.db.entities.directory.directory import DirectoryEntity


class _FakeSpyActivityDirectoryRepo(DirectoryFacadeABC):
    def __init__(self, note_ids: List[str]) -> None:
        self._note_ids = note_ids

    async def create_directory(self, entity, user_ctx):  # type: ignore[override]
        raise NotImplementedError()

    async def fetch_directory(self, id: str, *, include=None):  # type: ignore[override]
        raise NotImplementedError()

    async def update_directory(self, entity):  # type: ignore[override]
        raise NotImplementedError()

    async def delete_directory(self, entity):  # type: ignore[override]
        raise NotImplementedError()

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        return []

    async def fetch_directories_by_ids(
        self, ids: List[str],
    ) -> List[DirectoryEntity]:
        return []

    async def delete_directory(self, entity) -> bool:  # type: ignore[override]
        raise NotImplementedError()

    async def resolve_files_of_directory(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
    ) -> List[str]:
        return list(self._note_ids)

    async def resolve_subtree(
        self,
        directory_id: str,
        max_depth: int = 10,
    ) -> Tuple[List[str], List[str]]:
        return (list(self._note_ids), [directory_id])

    # ---- DirectoryHelperMixin: hierarchy helpers (no-op stubs) ------

    async def set_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
    ) -> None:
        return None

    async def get_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
    ) -> List[str]:
        return []

    async def get_children_of(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> List[str]:
        return []

    async def get_children_for(
        self,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> Dict[str, List[str]]:
        return {str(d): [] for d in parent_ids}

    async def get_parents_for(
        self,
        child_type: DirectoryChildType,
        child_ids: List[str],
        parent_type: DirectoryParentType,
    ) -> Dict[str, List[str]]:
        return {str(c): [] for c in child_ids}

    async def add_child_to(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        return None

    async def remove_child_from(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        return None


__all__ = [
    '_FakeSpyActivityDirectoryRepo',
]
