from __future__ import annotations

from tests.doubles.fakes.event_context import _E


class FakeDirectoryRepo:
    """Records every ``add_child_to`` call for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    async def add_child_to(
        self,
        parent_type: str,
        parent_id: str,
        child_type: str,
        child_id: str,
    ) -> None:
        self.calls.append((parent_type, parent_id, child_type, child_id))


class FakeTagRepo:
    """Records every ``assign_tag_to`` call for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def assign_tag_to(
        self, subject_type: str, subject_id: str, tag_id: str,
    ) -> None:
        self.calls.append((subject_type, subject_id, tag_id))


class FakeNoteContentRepo:
    """Maps note_id -> (title, content) for condition lookups."""

    def __init__(self, mapping: dict[str, tuple[str, str]] | None = None) -> None:
        self._mapping = mapping or {}

    async def select_by_id(self, note_id: str):
        # Minimal stand-in that only carries the two fields we need.
        class _E:
            def __init__(self, title, content):
                self.title = title
                self.content = content
        if note_id not in self._mapping:
            return _E(None, None)
        t, c = self._mapping[note_id]
        return _E(t, c)


__all__ = [
    'FakeDirectoryRepo',
    'FakeTagRepo',
    'FakeNoteContentRepo',
]
