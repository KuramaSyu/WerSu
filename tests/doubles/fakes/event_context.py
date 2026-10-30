from __future__ import annotations


class FakeNoteContentRepo:
    def __init__(self, mapping: dict[str, _E] | None = None) -> None:
        self._mapping = mapping or {}
        self.raise_on: set[str] = set()

    async def select_by_id(self, note_id: str):
        if note_id in self.raise_on:
            raise RuntimeError("boom")
        return self._mapping.get(note_id, _E())


class FakeDirectoryRepo:
    def __init__(self, parents: dict[str, list[str]] | None = None) -> None:
        # ``parents[child_id] = [parent_id_1, parent_id_2, ...]``
        # (only the first is followed in the chain walk; the
        # rest are ignored, mirroring the production behaviour).
        self._parents = parents or {}
        self.raise_on: set[str] = set()

    async def get_parents_of(self, child_type, child_id: str, parent_type):
        if child_id in self.raise_on:
            raise RuntimeError("boom")
        return list(self._parents.get(child_id, []))


class FakeShelfRepo:
    def __init__(self, books: dict[str, list[str]] | None = None) -> None:
        # ``books[shelf_id] = [book_id_1, ...]``
        self._books = books or {}
        self.raise_on: set[str] = set()

    async def get_books_of(self, shelf_id: str) -> list[str]:
        if shelf_id in self.raise_on:
            raise RuntimeError("boom")
        return list(self._books.get(shelf_id, []))


class _E:
    def __init__(self, title=None, content=None):
        self.title = title
        self.content = content


__all__ = [
    'FakeNoteContentRepo',
    'FakeDirectoryRepo',
    'FakeShelfRepo',
    '_E',
]
