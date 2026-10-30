from __future__ import annotations

from typing import List
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity


class FakeDirectoryFacade(DirectoryFacadeABC):
    def __init__(self) -> None:
        self._counter = 0
        self.created: List[DirectoryEntity] = []
        self._by_id = {}

    def _mint_id(self) -> str:
        self._counter += 1
        return f"dir-{self._counter}"

    async def create_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: UserContextABC = None,
    ) -> DirectoryEntity:
        new_id = self._mint_id()
        created = DirectoryEntity(
            id=new_id,
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
        )
        self.created.append(created)
        self._by_id[new_id] = created
        return created

    async def fetch_directories_by_ids(
        self, ids: List[str],
    ) -> List[DirectoryEntity]:
        out: List[DirectoryEntity] = []
        for did in ids:
            d = self._by_id.get(str(did))
            if d is not None:
                out.append(d)
        return out

    # Unused abstract stubs - raise loudly if accidentally hit.
    async def fetch_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def update_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_directory(self, *args, **kwargs) -> bool:
        raise NotImplementedError

    async def list_user_directory_ids(self, *args, **kwargs):
        raise NotImplementedError

    async def set_parents_of(self, *args, **kwargs) -> None:
        raise NotImplementedError

    async def get_parents_of(self, *args, **kwargs):
        raise NotImplementedError

    async def get_children_of(self, *args, **kwargs):
        raise NotImplementedError

    async def get_children_for(self, *args, **kwargs):
        raise NotImplementedError

    async def get_parents_for(self, *args, **kwargs):
        raise NotImplementedError

    async def add_child_to(self, *args, **kwargs) -> None:
        raise NotImplementedError

    async def remove_child_from(self, *args, **kwargs) -> None:
        raise NotImplementedError

    async def fetch_all_directories(self, *args, **kwargs):
        raise NotImplementedError

    async def resolve_files_of_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def resolve_subtree(self, *args, **kwargs):
        raise NotImplementedError


__all__ = [
    'FakeDirectoryFacade',
]
