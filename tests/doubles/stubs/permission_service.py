from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from src.api.other.types import Pagination
from src.api.other.user_context import UserContextABC
from src.db.entities import DirectoryEntity, NoteEntity
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
    DirectoryParentType,
)
from src.api.facades.note_facade import NoteFacadeABC, SearchType


class _StubPermissionNoteRepo(NoteFacadeABC):
    """Minimal note repo stub implementing only selection for existence checks."""

    def __init__(self, note_ids: set[str]) -> None:
        self._note_ids = note_ids

    async def insert(self, note: NoteEntity, user: UserContextABC) -> NoteEntity:
        raise NotImplementedError()

    async def update(self, note: NoteEntity, ctx: UserContext) -> NoteEntity:
        raise NotImplementedError()

    async def delete(self, note_id: str, ctx: UserContext) -> Optional[List[NoteEntity]]:
        raise NotImplementedError()

    async def select_by_id(self, note_id: str, ctx: UserContext) -> Optional[NoteEntity]:
        if note_id in self._note_ids:
            return NoteEntity(note_id=note_id)
        return None

    async def select_by_ids(
        self,
        note_ids: List[str],
        ctx: UserContext,
    ) -> List[NoteEntity]:
        return [
            NoteEntity(note_id=nid)
            for nid in note_ids
            if nid in self._note_ids
        ]

    async def search_notes(
        self,
        search_type: SearchType,
        query: str,
        ctx: UserContext,
        pagination: Pagination,
    ) -> List[NoteEntity]:
        raise NotImplementedError()


class _StubPermissionDirectoryRepo(DirectoryFacadeABC):
    """Minimal directory repo stub implementing only fetch for existence checks."""

    def __init__(self, directory_ids: set[str]) -> None:
        self._directory_ids = directory_ids

    async def create_directory(self, entity: DirectoryEntity) -> DirectoryEntity:
        raise NotImplementedError()

    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        if id in self._directory_ids:
            return DirectoryEntity(id=id, slug="test")
        return None

    async def update_directory(self, entity: DirectoryEntity) -> Optional[DirectoryEntity]:
        raise NotImplementedError()

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        raise NotImplementedError()

    async def fetch_all_directories(self) -> List[DirectoryEntity]:
        raise NotImplementedError()

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        raise NotImplementedError()

    async def fetch_directories_by_ids(
        self, ids: List[str],
    ) -> List[DirectoryEntity]:
        return []

    async def resolve_files_of_directory(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
    ) -> List[str]:
        return []

    async def resolve_subtree(
        self,
        directory_id: str,
        max_depth: int = 10,
    ) -> Tuple[List[str], List[str]]:
        return ([], [directory_id])

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
    '_StubPermissionNoteRepo',
    '_StubPermissionDirectoryRepo',
]
