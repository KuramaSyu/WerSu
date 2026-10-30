from __future__ import annotations


class _FakeDirectoryFacade:
    """Minimal directory facade stub for the bind-safety filter test.

    Only :meth:`fetch_directories_by_ids` is implemented; every
    other ``DirectoryFacadeABC`` member raises so an accidental
    call from the migration under test fails loudly in CI.
    """

    def __init__(self) -> None:
        self._existing_ids: set[str] = set()

    def add_directory(self, book_id: str) -> None:
        """Seed a directory id as existing."""
        self._existing_ids.add(str(book_id))

    async def fetch_directories_by_ids(
        self, ids: List[str],
    ):
        from src.db.entities.directory.directory import DirectoryEntity
        return [
            DirectoryEntity(id=bid)
            for bid in ids
            if str(bid) in self._existing_ids
        ]

    # Unused abstract stubs - raise loudly if accidentally hit.
    async def create_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def fetch_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def update_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_directory(self, *args, **kwargs) -> bool:
        raise NotImplementedError

    async def fetch_directories(self, *args, **kwargs):
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


__all__ = [
    '_FakeDirectoryFacade',
]
