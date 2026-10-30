from __future__ import annotations

from typing import List, Optional
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity


class FakeDirectoryFacade(DirectoryFacadeABC):
    """Minimal :class:`DirectoryFacadeABC` for tests.

    Implements only the methods the rule service and the chain
    reach for (``list_user_directory_ids``).  Every other method
    -- the abstract ones on :class:`DirectoryFacadeABC` and
    :class:`DirectoryHelperMixin` -- raises so that any future
    code path that accidentally uses them fails loudly rather
    than silently returning a fake value.
    """

    def __init__(
        self,
        viewable_directories: dict[str, List[str]] | None = None,
    ) -> None:
        # ``viewable_directories[user_id] = [directory_id, ...]``
        self._viewable = viewable_directories or {}

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        return list(self._viewable.get(user.user_id, []))

    # ---- abstract methods on the mixin (stubs) ----------------------

    async def set_parents_of(
        self,
        child_type,
        child_id: str,
        parent_type,
        parent_ids: List[str],
    ) -> None:
        raise NotImplementedError

    async def get_parents_of(
        self, child_type, child_id: str, parent_type,
    ) -> List[str]:
        raise NotImplementedError

    async def get_children_of(
        self, parent_type, parent_id: str, child_type, depth: int = 1,
    ) -> List[str]:
        raise NotImplementedError

    async def get_children_for(
        self, parent_type, parent_ids: List[str], child_type, depth: int = 1,
    ) -> dict[str, List[str]]:
        raise NotImplementedError

    async def get_parents_for(
        self, child_type, child_ids: List[str], parent_type,
    ) -> dict[str, List[str]]:
        raise NotImplementedError

    async def add_child_to(
        self, parent_type, parent_id: str, child_type, child_id: str,
    ) -> None:
        raise NotImplementedError

    async def remove_child_from(
        self, parent_type, parent_id: str, child_type, child_id: str,
    ) -> None:
        raise NotImplementedError

    # ---- abstract methods on the facade (stubs) ---------------------

    async def create_directory(
        self, entity: DirectoryEntity, user_ctx: UserContextABC,
    ) -> DirectoryEntity:
        raise NotImplementedError

    async def fetch_directory(
        self, directory_id: str, user_ctx: UserContextABC, *,
        include: object = None,
    ) -> Optional[DirectoryEntity]:
        raise NotImplementedError

    async def update_directory(
        self, entity: DirectoryEntity, user_ctx: UserContextABC,
    ) -> DirectoryEntity:
        raise NotImplementedError

    async def delete_directory(
        self, entity: DirectoryEntity, user_ctx: UserContextABC,
    ) -> None:
        raise NotImplementedError

    async def fetch_directories_by_ids(
        self, ids: List[str],
    ) -> List[DirectoryEntity]:
        return []


__all__ = [
    'FakeDirectoryFacade',
]
