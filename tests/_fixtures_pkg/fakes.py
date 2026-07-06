"""In-memory test doubles extracted from ``tests/fixtures.py``.

These classes are not pytest fixtures — they are *factories* that
fixtures compose.  Keeping them out of ``fixtures.py`` is what lets
that module stay declarative and short.

Names retain their leading underscore so existing test modules that
import them via ``from tests.fixtures import _FakeEmbeddingRepo`` still
work (the parent ``tests.fixtures`` package re-exports them).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.database import DatabaseABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.versioning import NoteVersionEntry
from src.api.directory_repo import DirectoryRepo
from src.db.repos.note.content import NoteContentRepo
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.db.repos.note.note import NoteRepoFacadeABC, SearchType
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
    """In-memory directory repo used by unit tests.

    The default behaviour (used by every existing test that just wants
    *something* resolvable per user) seeds one directory per user id:
    ``"<default-name>-<user_id>"``.  Tests that need to drive
    create / update / delete / lookups by id, or a richer directory
    set, can populate ``self.directories_by_id`` /
    ``self.user_to_directory_ids`` directly.
    """

    def __init__(self) -> None:
        # ``directories_by_id``: directory id -> DirectoryEntity.  Tests
        # can pre-populate this to control what ``fetch_directory``
        # returns.
        self.directories_by_id: Dict[str, DirectoryEntity] = {}
        # ``user_to_directory_ids``: user id -> list of directory ids.
        # When populated, ``list_user_directory_ids`` returns the
        # corresponding ids; otherwise the legacy fallback of a single
        # default directory is used so older tests keep working.
        self.user_to_directory_ids: Dict[str, List[str]] = {}
        # ``subtree_by_root``: root directory id ->
        # ``(note_ids, directory_ids)``.  Tests that need a populated
        # subtree seed this so ``resolve_subtree`` returns their ids.
        # Roots not in the dict fall back to ``([], [directory_id])``.
        self.subtree_by_root: Dict[str, Tuple[List[str], List[str]]] = {}
        # Recorded calls for assertions.
        self.created: List[DirectoryEntity] = []
        self.updated: List[DirectoryEntity] = []
        self.deleted: List[str] = []
        self._next_directory_id = 0

    @property
    def _default_directory_name(self) -> str:
        return self.get_default_directory_specs()[0].name

    def _seed_default_directory(self, user: UserContextABC) -> str:
        """Return and lazily create the default directory for ``user``."""
        directory_id = f"{self._default_directory_name}-{user.user_id}"
        if directory_id not in self.directories_by_id:
            self.directories_by_id[directory_id] = DirectoryEntity(
                id=directory_id, name=self._default_directory_name
            )
        return directory_id

    async def create_directory(self, entity: DirectoryEntity) -> DirectoryEntity:
        self._next_directory_id += 1
        new_id = entity.id if entity.id not in (None, UNDEFINED) else f"dir-{self._next_directory_id}"
        created = DirectoryEntity(
            id=new_id,
            name=entity.name,
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            parent_id=entity.parent_id,
            readme_note_id=entity.readme_note_id,
            relations=entity.relations,
        )
        self.directories_by_id[str(new_id)] = created
        self.created.append(created)
        return created

    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        existing = self.directories_by_id.get(id)
        if existing is not None:
            return existing
        # Fall back to the legacy stub so older tests that only ever
        # asked for the default directory keep working.
        return DirectoryEntity(id=id, name=self._default_directory_name)

    async def update_directory(self, entity: DirectoryEntity) -> Optional[DirectoryEntity]:
        self.updated.append(entity)
        existing = self.directories_by_id.get(str(entity.id))
        if existing is None:
            return None
        updated = DirectoryEntity(
            id=existing.id,
            name=existing.name if entity.name is UNDEFINED else entity.name,
            display_name=existing.display_name if entity.display_name is UNDEFINED else entity.display_name,
            description=existing.description if entity.description is UNDEFINED else entity.description,
            image_url=existing.image_url if entity.image_url is UNDEFINED else entity.image_url,
            parent_id=existing.parent_id if entity.parent_id is UNDEFINED else entity.parent_id,
            readme_note_id=existing.readme_note_id if entity.readme_note_id is UNDEFINED else entity.readme_note_id,
            relations=existing.relations,
        )
        self.directories_by_id[str(entity.id)] = updated
        return updated

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        if user.user_id in self.user_to_directory_ids:
            return list(self.user_to_directory_ids[user.user_id])
        # Default fallback: one default-named directory per user.
        return [self._seed_default_directory(user)]

    async def fetch_all_directories(self) -> List[DirectoryEntity]:
        return list(self.directories_by_id.values())

    async def list_note_directory_ids(self, note_id: str) -> List[str]:
        return []

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        directory_id = str(entity.id)
        self.deleted.append(directory_id)
        if directory_id in self.directories_by_id:
            del self.directories_by_id[directory_id]
        return True

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
        """In-memory stub.

        Returns the seeded ``(note_ids, directory_ids)`` for
        ``directory_id`` from :attr:`subtree_by_root`, or the root
        alone when no seed is present.
        """
        return self.subtree_by_root.get(directory_id, ([], [directory_id]))


class _FakeNoteRepoFacade(NoteRepoFacadeABC):
    """In-memory :class:`NoteRepoFacadeABC` used by unit tests.

    Stores notes by id; ``insert`` mints a sequential id, every other
    CRUD method delegates to the in-memory store.  Tests can drive
    behaviour by populating ``notes_by_id`` ahead of time or by
    reading ``insert_calls`` / ``select_calls`` for assertions.
    """

    def __init__(self) -> None:
        self.notes_by_id: Dict[str, NoteEntity] = {}
        self.select_calls: List[str] = []
        self.insert_calls: List[NoteEntity] = []
        self.update_calls: List[NoteEntity] = []
        self.delete_calls: List[str] = []
        self.search_calls: List[tuple] = []
        self._next_note_id = 0

    async def insert(self, note: NoteEntity, user: UserContextABC) -> NoteEntity:
        self.insert_calls.append(note)
        self._next_note_id += 1
        if note.note_id in (None, UNDEFINED):
            note.note_id = f"note-{self._next_note_id}"
        if note.permissions is UNDEFINED:
            note.permissions = []
        self.notes_by_id[str(note.note_id)] = note
        return note

    async def update(self, note: NoteEntity, ctx: UserContextABC) -> NoteEntity:
        self.update_calls.append(note)
        existing = self.notes_by_id.get(str(note.note_id))
        if existing is not None:
            self.notes_by_id[str(note.note_id)] = note
        return note

    async def delete(self, note_id: str, ctx: UserContextABC) -> Optional[List[NoteEntity]]:
        self.delete_calls.append(str(note_id))
        existing = self.notes_by_id.pop(str(note_id), None)
        return [existing] if existing is not None else None

    async def select_by_id(self, note_id: str, ctx: UserContextABC) -> Optional[NoteEntity]:
        self.select_calls.append(str(note_id))
        return self.notes_by_id.get(str(note_id))

    async def search_notes(
        self,
        search_type: "SearchType",
        query: str,
        ctx: UserContextABC,
        pagination,
    ) -> List[NoteEntity]:
        self.search_calls.append((search_type, query, pagination))
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
    "_FakeNoteRepoFacade",
    "_FakeDatabase",
    "_FakeJwtProvider",
    "_FakeVersionRepo",
    "_TestDirectoryRepo",
    "_TestSpiceDbClient",
]
