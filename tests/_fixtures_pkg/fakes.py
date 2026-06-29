"""In-memory test doubles extracted from ``tests/fixtures.py``.

These classes are not pytest fixtures — they are *factories* that
fixtures compose.  Keeping them out of ``fixtures.py`` is what lets
that module stay declarative and short.

Names retain their leading underscore so existing test modules that
import them via ``from tests.fixtures import _FakeEmbeddingRepo`` still
work (the parent ``tests.fixtures`` package re-exports them).
"""

from __future__ import annotations

from typing import Any, List, Optional

from src.api.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.entities.note.versioning import NoteVersionEntry
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.db.repos.note.versioning import NoteVersionRepoABC


class _FakeEmbeddingGenerator:
    """Lightweight embedding generator to avoid heavy ML model loads."""

    @property
    def model_name(self) -> str:
        return "fake"

    def generate(self, text: str) -> Any:
        return [0.0]

    def tensor_to_sequence(self, tensor: Any) -> List[float]:
        return [0.0]


class _FakeEmbeddingRepo(NoteEmbeddingRepo):
    """Stub embedding repo used by tests that don't need real ML embeddings."""

    def __init__(self) -> None:
        self._generator = _FakeEmbeddingGenerator()

    @property
    def embedding_generator(self) -> _FakeEmbeddingGenerator:
        return self._generator

    async def insert(self, note_id: str, title: str, content: str) -> NoteEmbeddingEntity:
        return NoteEmbeddingEntity(note_id=note_id, model="fake", embedding=[0.0])

    async def _update(self, set: NoteEmbeddingEntity, where: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        return NoteEmbeddingEntity(note_id=where.note_id, model="fake", embedding=[0.0])

    async def update(self, note_id: str, title: str, content: str) -> NoteEmbeddingEntity:
        return NoteEmbeddingEntity(note_id=note_id, model="fake", embedding=[0.0])

    async def delete(self, embedding: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        return embedding

    async def select(self, embedding: NoteEmbeddingEntity) -> List[NoteEmbeddingEntity]:
        return [embedding]


class _FakeVersionRepo(NoteVersionRepoABC):
    """Stub version repo that returns predefined entries per note."""

    def __init__(self, entries: Optional[dict[str, NoteVersionEntry]] = None) -> None:
        self._entries = entries or {}

    @property
    def max_deltas_per_snapshot(self) -> int:
        return 0

    async def record_initial_snapshot(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError()

    async def append_version(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError()

    async def list_versions(self, note_id: str, limit: int, offset: int) -> List[NoteVersionEntry]:
        entry = self._entries.get(note_id)
        if entry is None:
            return []
        return [entry]

    async def get_content_at_version(self, note_id: str, version_index: int):  # type: ignore[override]
        raise NotImplementedError()


class _TestDirectoryRepo(DirectoryRepo):
    """In-memory directory repo used by unit tests."""

    @property
    def _default_directory_name(self) -> str:
        return self.get_default_directory_specs()[0].name

    async def create_directory(self, entity: DirectoryEntity) -> DirectoryEntity:
        raise NotImplementedError()

    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        return DirectoryEntity(id=id, name=self._default_directory_name)

    async def update_directory(self, entity: DirectoryEntity) -> Optional[DirectoryEntity]:
        return entity

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        return [f"{self._default_directory_name}-{user.user_id}"]

    async def fetch_all_directories(self) -> List[DirectoryEntity]:
        return []

    async def list_note_directory_ids(self, note_id: str) -> List[str]:
        return []

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        raise NotImplementedError()

    async def resolve_files_of_directory(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
    ) -> List[str]:
        return []


class _TestSpiceDbClient:
    """No-op SpiceDB client used by the ``db`` fixture for migration setup."""

    async def WriteSchema(self, request) -> None:
        return None


__all__ = [
    "_FakeEmbeddingGenerator",
    "_FakeEmbeddingRepo",
    "_FakeVersionRepo",
    "_TestDirectoryRepo",
    "_TestSpiceDbClient",
]
