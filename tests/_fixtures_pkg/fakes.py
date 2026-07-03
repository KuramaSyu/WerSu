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
from src.db.database import DatabaseABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.versioning import NoteVersionEntry
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.content import NoteContentRepo
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
        self.last_snapshot: Optional[dict] = None
        self.last_append: Optional[dict] = None

    @property
    def max_deltas_per_snapshot(self) -> int:
        return 0

    async def record_initial_snapshot(self, *args, **kwargs):  # type: ignore[override]
        self.last_snapshot = {"args": args, "kwargs": kwargs}
        return None

    async def append_version(self, *args, **kwargs):  # type: ignore[override]
        self.last_append = {"args": args, "kwargs": kwargs}
        return None

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


class _FakeDatabase(DatabaseABC):
    """In-memory :class:`DatabaseABC` used by pure unit tests.

    Only the methods :class:`src.db.repos.note.note.NoteFacade`
    and the search strategies call are implemented; the rest raise
    to make accidental use loud.  Tests queue the responses they
    expect to see.
    """

    def __init__(self) -> None:
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchrow_responses: list[dict] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetch_responses: list[list[dict]] = []

    async def init_db(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @property
    def pool(self):
        raise NotImplementedError("_FakeDatabase has no asyncpg pool")

    async def execute(self, query: str, *args) -> str:
        raise NotImplementedError("_FakeDatabase.execute is not implemented")

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        if not self.fetch_responses:
            raise AssertionError("_FakeDatabase.fetch called without a queued response")
        return self.fetch_responses.pop(0)

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        if not self.fetchrow_responses:
            raise AssertionError("_FakeDatabase.fetchrow called without a queued response")
        return self.fetchrow_responses.pop(0)


class _FakeNoteContentRepo(NoteContentRepo):
    """In-memory :class:`NoteContentRepo` used by pure unit tests.

    Implements the three methods :class:`src.db.repos.note.note.NoteFacade`
    uses: :meth:`select_by_id`, :meth:`update`, :meth:`delete`.
    """

    def __init__(self) -> None:
        self._store: dict[str, NoteEntity] = {}

    def seed(self, note: NoteEntity) -> None:
        """Insert `note` into the in-memory store keyed by its `note_id`."""
        self._store[str(note.note_id)] = note

    async def insert(self, metadata: NoteEntity) -> NoteEntity:
        self._store[str(metadata.note_id)] = metadata
        return metadata

    async def update(self, set: NoteEntity, where: NoteEntity) -> NoteEntity:
        existing = self._store.get(str(where.note_id))
        if existing is None:
            raise RuntimeError(f"note {where.note_id!r} not found")
        merged = NoteEntity(
            note_id=existing.note_id,
            title=set.title if set.title is not None else existing.title,
            content=set.content if set.content is not None else existing.content,
            updated_at=set.updated_at if set.updated_at is not None else existing.updated_at,
            author_id=set.author_id if set.author_id is not None else existing.author_id,
            embeddings=existing.embeddings,
            permissions=existing.permissions,
        )
        self._store[str(merged.note_id)] = merged
        return merged

    async def delete(self, metadata: NoteEntity) -> List[NoteEntity]:
        existing = self._store.pop(str(metadata.note_id), None)
        return [existing] if existing is not None else []

    async def select(self, metadata: NoteEntity) -> List[NoteEntity]:
        return [n for n in self._store.values() if n.note_id == metadata.note_id]

    async def select_by_id(self, note_id: str) -> NoteEntity:
        existing = self._store.get(str(note_id))
        if existing is None:
            raise RuntimeError(f"note {note_id!r} not found")
        return existing


class _FakeJwtProvider:
    """Stub :class:`~src.api.jwt_provider.JwtProvider` that returns deterministic tokens."""

    def __init__(self) -> None:
        self.create_calls: list[tuple[str, str]] = []

    def create_attachment_token(
        self,
        user_id: str,
        attachment_id: str,
        *,
        ttl_seconds: int = 15 * 60,
    ) -> str:
        self.create_calls.append((user_id, attachment_id))
        return f"jwt:{user_id}:{attachment_id}"

    def verify_attachment_token(self, token: str, *, expected_attachment_id: str):
        raise NotImplementedError


__all__ = [
    "_FakeEmbeddingGenerator",
    "_FakeEmbeddingRepo",
    "_FakeNoteContentRepo",
    "_FakeDatabase",
    "_FakeJwtProvider",
    "_FakeVersionRepo",
    "_TestDirectoryRepo",
    "_TestSpiceDbClient",
]
