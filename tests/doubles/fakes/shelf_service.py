from __future__ import annotations

from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from typing import List, Optional


class FakeDirectoryFacade(DirectoryFacadeABC):
    """Minimal :class:`DirectoryFacadeABC` for tests.

    The shelf service only consults ``DEFAULT_DIRECTORY_SPECS``
    (a class-level tuple) and forwards ``create_directory`` to
    whatever's here.  We capture the created entities and stash
    them with a deterministic id so the strategy's
    ``get_default_directory_specs`` loop sees the same shape
    production does.
    """

    def __init__(self) -> None:
        self._counter = 0
        self.created: List[DirectoryEntity] = []

    def _mint_id(self) -> str:
        self._counter += 1
        return f"dir-{self._counter}"

    async def create_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: Optional[UserContextABC] = None,
    ) -> DirectoryEntity:
        created = DirectoryEntity(
            id=self._mint_id(),
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
            relations=list(entity.relations or []),
        )
        self.created.append(created)
        return created

    # ---- abstract methods on the mixin / facade: stubs -------------

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

    async def list_user_directory_ids(self, *args, **kwargs):
        raise NotImplementedError

    async def fetch_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def update_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_directory(self, *args, **kwargs) -> bool:
        raise NotImplementedError

    async def fetch_all_directories(self, *args, **kwargs):
        raise NotImplementedError

    async def fetch_directories_by_ids(
        self, ids: List[str],
    ) -> List[DirectoryEntity]:
        # Mirror :meth:`_TestDirectoryRepo`'s behaviour: return
        # whatever matching directories the test pre-seeded into
        # the facade's ``created`` list (the only directories this
        # fake knows about).  Missing ids silently drop.
        out: List[DirectoryEntity] = []
        for did in ids:
            for d in self.created:
                if str(d.id) == str(did):
                    out.append(d)
                    break
        return out

    async def resolve_files_of_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def resolve_subtree(self, *args, **kwargs):
        raise NotImplementedError


__all__ = [
    'FakeDirectoryFacade',
]
