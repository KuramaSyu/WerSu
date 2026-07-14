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

from src.api.repos.combined_note_repo import CombinedNoteRepoABC
from src.api.repos.tag_repo import TagRepoABC, TagSubjectType
from src.api.services.directory_service import DirectoryIncludeOptions
from src.api.services.note_service import NoteIncludeOptions
from src.api.other.undefined import UNDEFINED, is_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db.database import DatabaseABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.tag import TagEntity
from src.db.entities.note.versioning import NoteVersionEntry
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.other.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.db.repos.note.content import NoteContentRepo
from src.db.table import TableABC
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.api.facades.note_facade import NoteRepoFacadeABC, SearchType
from src.api.services.note_service import NoteResponse, NoteServiceABC
from src.services.attachment_facade import Attachment, AttachmentFacadeABC
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


class _TestDirectoryRepo(DirectoryFacadeABC):
    """In-memory directory repo used by unit tests.

    The default behaviour (used by every existing test that just wants
    *something* resolvable per user) seeds one directory per user id:
    ``"<default-name>-<user_id>"``.  Tests that need to drive
    create / update / delete / lookups by id, or a richer directory
    set, can populate ``self.directories_by_id`` /
    ``self.user_to_directory_ids`` directly.
    """

    def __init__(self, *, permission_repo: Optional[PermissionRepoABC] = None) -> None:
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
        # ``note_to_directory_ids``: note id -> list of parent directory
        # ids.  Populated by :meth:`add_note_to_directory` so
        # :meth:`list_note_directory_ids` returns the bindings the test
        # just established.
        self.note_to_directory_ids: Dict[str, List[str]] = {}
        # Recorded calls for assertions.
        self.created: List[DirectoryEntity] = []
        self.updated: List[DirectoryEntity] = []
        self.deleted: List[str] = []
        self._next_directory_id = 0
        # Optional permission repo reference so that ``create_directory``
        # can mirror the production behaviour of writing the relations
        # the entity carries.  Without this, tests that use the real
        # :class:`DirectoryServiceImpl` would fail every visibility check
        # inside :class:`NoteServiceImpl` / :class:`NoteFacadeImpl`.
        self._permission_repo = permission_repo

    @property
    def _default_directory_name(self) -> str:
        return self.get_default_directory_specs()[0].name

    def _seed_default_directory(self, user: UserContextABC) -> str:
        """Return and lazily create the default directory for ``user``."""
        directory_id = f"{self._default_directory_name}-{user.user_id}"
        if directory_id not in self.directories_by_id:
            self.directories_by_id[directory_id] = DirectoryEntity(
                id=directory_id, slug=self._default_directory_name
            )
        return directory_id

    async def create_directory(self, entity: DirectoryEntity, user_ctx: UserContextABC = None) -> DirectoryEntity:
        self._next_directory_id += 1
        new_id = entity.id if entity.id not in (None, UNDEFINED) else f"dir-{self._next_directory_id}"
        created = DirectoryEntity(
            id=new_id,
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            readme_note_id=entity.readme_note_id,
            parent_directory_ids=entity.parent_directory_ids,
            tag_ids=entity.tag_ids,
            relations=entity.relations,
        )
        self.directories_by_id[str(new_id)] = created
        self.created.append(created)

        # Mirror the production :class:`DirectoryFacadeImpl`
        # behaviour: write the entity's relations through the permission
        # repo so that subsequent reads see the new directory as
        # visible to the caller.
        if self._permission_repo is not None and entity.relations:
            resolved = []
            for rel in entity.relations:
                resolved.append(
                    Relationship(
                        resource=ObjectRef(
                            object_type=rel.resource.object_type,
                            object_id=str(new_id),
                        ),
                        relation=rel.relation,
                        subject=rel.subject,
                    )
                )
            await self._permission_repo.insert(resolved)

        # Mirror the production :class:`DirectoryFacadeImpl` behaviour
        # of always attaching a ``dir#admin@user`` relation for the
        # caller.  Production writes it to the permission repo and
        # returns it on ``created.relations``; do the same here so
        # callers can inspect the relation without poking the
        # permission repo themselves.
        if self._permission_repo is not None and user_ctx is not None:
            admin_relation = Relationship(
                resource=ObjectRef(
                    object_type=ObjectTypeEnum.DIRECTORY,
                    object_id=str(new_id),
                ),
                relation=DirectoryRelationEnum.ADMIN,
                subject=SubjectRef(
                    object_type=ObjectTypeEnum.USER,
                    object_id=str(user_ctx.user_id),
                ),
            )
            await self._permission_repo.insert([admin_relation])
            if not isinstance(created.relations, list):
                created.relations = []
            created.relations.append(admin_relation)

        # Mirror the parent relations written when
        # ``entity.parent_directory_ids`` is set, matching the
        # production repo.
        parent_ids = entity.parent_directory_ids
        if (
            self._permission_repo is not None
            and parent_ids not in (UNDEFINED, None)
            and len(parent_ids) > 0
        ):
            for parent_id in parent_ids:
                if not parent_id:
                    continue
                await self._permission_repo.insert(
                    [
                        Relationship(
                            resource=ObjectRef(
                                object_type=ObjectTypeEnum.DIRECTORY,
                                object_id=str(new_id),
                            ),
                            relation=DirectoryRelationEnum.PARENT,
                            subject=SubjectRef(
                                object_type=ObjectTypeEnum.DIRECTORY,
                                object_id=str(parent_id),
                            ),
                        )
                    ]
                )

        return created

    async def fetch_directory(
        self,
        id: str,
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> Optional[DirectoryEntity]:
        existing = self.directories_by_id.get(id)
        if existing is None:
            existing = DirectoryEntity(
                id=id, slug=self._default_directory_name
            )
        # No aggregate counts in this fake -- callers derive them
        # from `len(child_directory_ids)` / `len(child_note_ids)` when
        # those lists are populated.
        return existing

    async def update_directory(self, entity: DirectoryEntity) -> Optional[DirectoryEntity]:
        self.updated.append(entity)
        existing = self.directories_by_id.get(str(entity.id))
        if existing is None:
            return None
        updated = DirectoryEntity(
            id=existing.id,
            slug=existing.slug if is_undefined(entity.slug) else entity.slug,
            display_name=existing.display_name if is_undefined(entity.display_name) else entity.display_name,
            description=existing.description if is_undefined(entity.description) else entity.description,
            image_url=existing.image_url if is_undefined(entity.image_url) else entity.image_url,
            readme_note_id=existing.readme_note_id if is_undefined(entity.readme_note_id) else entity.readme_note_id,
            parent_directory_ids=(
                existing.parent_directory_ids
                if is_undefined(entity.parent_directory_ids)
                else entity.parent_directory_ids
            ),
            tag_ids=existing.tag_ids if is_undefined(entity.tag_ids) else entity.tag_ids,
            relations=existing.relations,
        )
        self.directories_by_id[str(entity.id)] = updated
        return updated

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        # Start from the test-managed map (always wins).
        if user.user_id in self.user_to_directory_ids:
            direct = set(self.user_to_directory_ids[user.user_id])
        else:
            direct = {self._seed_default_directory(user)}

        # Mirror the production behaviour: ``admin``, ``writer``,
        # ``reader`` and ``owner`` relations on a directory all imply
        # ``view`` (see :data:`docs.spicedb-schema`), so any directory
        # the user has one of those relations on is visible to them.
        # The in-memory :class:`InMemoryPermissionRepo` does not
        # expand transitive implications, so this fallback lives on
        # the directory repo instead.
        if self._permission_repo is not None:
            for rel in getattr(self._permission_repo, "_store", []):
                try:
                    if (
                        str(rel.resource.object_type)
                        == ObjectTypeEnum.DIRECTORY.value
                        and str(rel.subject.object_type)
                        == ObjectTypeEnum.USER.value
                        and str(rel.subject.object_id) == user.user_id
                        and str(rel.relation)
                        in {"admin", "writer", "reader", "owner"}
                        and rel.resource.object_id is not None
                    ):
                        direct.add(str(rel.resource.object_id))
                except Exception:
                    # Tolerate half-constructed relationships.
                    continue
        return sorted(direct)

    async def fetch_all_directories(self) -> List[DirectoryEntity]:
        return list(self.directories_by_id.values())

    async def list_note_directory_ids(self, note_id: str) -> List[str]:
        """Return the dirs the note was added to via ``add_note_to_directory``.

        Production :class:`DirectoryFacadeImpl.list_note_directory_ids`
        queries the permission repo for ``parent_directory``
        relations; the fake tracks the same set locally so tests
        that drive :meth:`add_note_to_directory` see the matching
        ids on subsequent reads.
        """
        return sorted(self.note_to_directory_ids.get(str(note_id), []))

    async def add_note_to_directory(self, note_id: str, directory_id: str) -> None:
        """Stub that mirrors the production `parent_directory` write.

        Production
        :class:`DirectoryFacadeImpl.add_note_to_directory` writes both
        the Postgres hierarchy row and the SpiceDB `parent_directory`
        relation.  Tests use this stub instead of a real Postgres
        table, but they still need to observe the `parent_directory`
        relation through whatever permission repo the test wired --
        so we mirror the relation here when a permission repo is
        available.  The in-memory ``note_to_directory_ids`` map
        also tracks the binding so subsequent
        :meth:`list_note_directory_ids` reads see it.
        """
        self.note_to_directory_ids.setdefault(str(note_id), [])
        if str(directory_id) not in self.note_to_directory_ids[str(note_id)]:
            self.note_to_directory_ids[str(note_id)].append(str(directory_id))
        if self._permission_repo is not None:
            await self._permission_repo.insert(
                [
                    Relationship(
                        resource=ObjectRef(
                            object_type="note",
                            object_id=str(note_id),
                        ),
                        relation="parent_directory",
                        subject=SubjectRef(
                            object_type="directory",
                            object_id=str(directory_id),
                        ),
                    )
                ]
            )

    async def remove_note_from_directory(self, note_id: str, directory_id: str) -> None:
        """Stub mirroring production by deleting the SpiceDB relation.

        Mirrors :class:`DirectoryFacadeImpl.remove_note_to_directory`,
        which also clears the `parent_directory` relation in SpiceDB.
        """
        if str(directory_id) in self.note_to_directory_ids.get(str(note_id), []):
            self.note_to_directory_ids[str(note_id)].remove(str(directory_id))
        if self._permission_repo is not None:
            await self._permission_repo.delete(
                Relationship(
                    resource=ObjectRef(
                        object_type="note",
                        object_id=str(note_id),
                    ),
                    relation="parent_directory",
                    subject=SubjectRef(
                        object_type="directory",
                        object_id=str(directory_id),
                    ),
                )
            )

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

    async def _count_direct_child_directories_for(
        self, directory_id: str,
    ) -> int:
        """Internal helper: count direct child directories via the in-memory store."""
        if self._permission_repo is None:
            return 0
        return len(
            await self._permission_repo.lookup(
                Relationship(
                    resource=ObjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY, object_id=UNDEFINED
                    ),
                    relation=DirectoryRelationEnum.PARENT,
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY, object_id=str(directory_id)
                    ),
                )
            )
        )

    async def _count_direct_child_notes_for(
        self, directory_id: str,
    ) -> int:
        """Internal helper: count direct child notes via the in-memory store."""
        if self._permission_repo is None:
            return 0
        return len(
            await self._permission_repo.lookup(
                Relationship(
                    resource=ObjectRef(
                        object_type=ObjectTypeEnum.NOTE, object_id=UNDEFINED
                    ),
                    relation=NoteRelationEnum.PARENT_DIRECTORY,
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY, object_id=str(directory_id)
                    ),
                )
            )
        )

    async def resolve_subtree(
        self, directory_id: str, *, max_depth: int = 10,
    ) -> tuple[list[str], list[str]]:
        """Return seeded subtree ids for `directory_id`.

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

    async def select_by_id(
        self,
        note_id: str,
        ctx: UserContextABC,
        *,
        include_permissions: bool = True,
    ) -> Optional[NoteEntity]:
        del ctx, include_permissions
        self.select_calls.append(str(note_id))
        return self.notes_by_id.get(str(note_id))

    async def select_by_ids(
        self,
        note_ids: List[str],
        ctx: UserContextABC,
        *,
        include_permissions: bool = True,
    ) -> List[NoteEntity]:
        del ctx, include_permissions
        self.select_calls.append(list(note_ids))
        results: List[NoteEntity] = []
        for nid in note_ids:
            note = self.notes_by_id.get(nid)
            if note is None:
                raise ValueError(
                    f"Notes with ids {nid!r} could not be resolved"
                )
            results.append(note)
        return results

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

    Only the methods :class:`src.db.repos.note.note.NoteFacadeImpl`
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
    """In-memory NoteContentRepo used by pure unit tests."""

    def __init__(self, facade: Optional["_FakeNoteRepoFacade"] = None) -> None:
        self._store: dict[str, NoteEntity] = {}
        self._facade = facade
        self._next_id = 0

    def _lookup(self, note_id: str) -> Optional[NoteEntity]:
        existing = self._store.get(str(note_id))
        if existing is not None:
            return existing
        if self._facade is not None:
            return self._facade.notes_by_id.get(str(note_id))
        return None

    def seed(self, note: NoteEntity) -> None:
        """Insert `note` into the in-memory store keyed by its `note_id`."""
        self._store[str(note.note_id)] = note

    async def insert(self, metadata: NoteEntity) -> NoteEntity:
        # Mirror production: when the caller doesn't supply an id,
        # mint one so the facade can carry on with embedding / tags /
        # permission writes.  Otherwise keep what the caller passed.
        if is_undefined(metadata.note_id) or metadata.note_id is None:
            self._next_id += 1
            new_id = f"note-{self._next_id:04d}"
            metadata = NoteEntity(
                note_id=new_id,
                title=metadata.title,
                updated_at=metadata.updated_at,
                author_id=metadata.author_id,
                content=metadata.content,
                directory_ids=unwrap_undefined_or(metadata.directory_ids, []),
                tag_ids=unwrap_undefined_or(metadata.tag_ids, []),
                embeddings=unwrap_undefined_or(metadata.embeddings, []),
                permissions=unwrap_undefined_or(metadata.permissions, []),
            )
        self._store[str(metadata.note_id)] = metadata
        return metadata

    async def update(self, set: NoteEntity, where: NoteEntity) -> NoteEntity:
        existing = self._lookup(str(where.note_id))
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
        if existing is None and self._facade is not None:
            existing = self._facade.notes_by_id.pop(str(metadata.note_id), None)
        return [existing] if existing is not None else []

    async def select(self, metadata: NoteEntity) -> List[NoteEntity]:
        return [n for n in self._store.values() if n.note_id == metadata.note_id]

    async def select_by_id(self, note_id: str) -> NoteEntity:
        existing = self._lookup(note_id)
        if existing is None:
            raise RuntimeError(f"note {note_id!r} not found")
        return existing

    async def select_by_ids(self, note_ids: List[str]) -> List[NoteEntity]:
        if not note_ids:
            raise ValueError("note_ids must not be empty")
        missing = [nid for nid in note_ids if self._lookup(nid) is None]
        if missing:
            raise ValueError(
                f"Notes with ids {missing!r} could not be resolved"
            )
        return [self._lookup(str(nid)) for nid in note_ids]  # type: ignore[arg-type]  # noqa: F821


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


class _FakeDirectorySubdirectoryTable(TableABC):
    """In-memory implementation of ``note.directory_subdirectory``.

    Mirrors the SQL semantics the
    :class:`~src.db.repos.directory.postgres.PostgresDirectoryRepo`
    uses:

    * rows are ``(directory_id, child_directory_id)`` tuples,
    * inserts are idempotent via the same ``on_conflict`` flag the
      production repo passes, and
    * the ``fetch(clause, *args, select=...)`` parser does a tiny
      ``WHERE directory_id = $1`` match to keep the helper usable
      from the production code path without spinning up Postgres.

    Tests can populate rows up front via the ``rows`` attribute or
    by calling :meth:`add_directory_child`.
    """

    name = "note.directory_subdirectory"

    def __init__(self) -> None:
        # each entry: (directory_id, child_directory_id)
        self.rows: set[tuple[str, str]] = set()
        self._id_counter = 0

    def get_id_fields(self) -> List[str]:
        return ["id"]

    async def insert(
        self,
        where: Dict[str, Any],
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[List[Any]]:
        directory_id = where.get("directory_id")
        child_directory_id = where.get("child_directory_id")
        self._id_counter += 1
        self.rows.add((directory_id, child_directory_id))
        return [
            {
                "id": self._id_counter,
                "directory_id": directory_id,
                "child_directory_id": child_directory_id,
            }
        ]

    async def update(self, set, where, returning: str = "*"):
        # Hierarchy rows are immutable in the production schema; the
        # helper exposes ``update`` so the repo's call sites compile,
        # but no test currently needs it.
        return []

    async def delete(
        self,
        where: Dict[str, Any],
        returning: str = "*",
    ) -> Optional[List[Any]]:
        directory_id = where.get("directory_id")
        child_directory_id = where.get("child_directory_id")
        before = len(self.rows)
        self.rows = {
            row
            for row in self.rows
            if not (
                (directory_id is None or row[0] == directory_id)
                and (child_directory_id is None or row[1] == child_directory_id)
            )
        }
        after = len(self.rows)
        return [] if before == after else [{"removed": True}]

    async def select(self, where=None, order_by=None, select: str = "*", additional_values=None):
        directory_id = (where or {}).get("child_directory_id")
        items = []
        for parent, child in sorted(self.rows):
            if directory_id is not None and child != directory_id:
                continue
            items.append({"directory_id": parent, "child_directory_id": child})
        return items

    async def select_row(self, where, select: str = "*"):
        return None

    async def delete_by_id(self, *id_values: Any):
        return None

    async def fetch_by_id(self, *id_values: Any, select: str = "*"):
        return None

    async def fetch(self, clause: str, *args, **kwargs):
        matches = list(self.rows)
        if args:
            directory_id = args[0]
            matches = [r for r in matches if r[0] == directory_id]
        return [
            {
                "directory_id": parent,
                "child_directory_id": child,
            }
            for parent, child in matches
        ]

    async def execute(self, sql: str, *args):
        return await self.fetch(sql, *args)

    def add_directory_child(self, parent_id: str, child_id: str) -> None:
        """Seed a ``directory_id -> child_directory_id`` row."""
        self.rows.add((parent_id, child_id))


class _FakeDirectoryNoteTable(TableABC):
    """In-memory implementation of ``note.directory_note``.

    Mirrors the SQL semantics the
    :class:`~src.db.repos.directory.postgres.PostgresDirectoryRepo`
    uses:

    * rows are ``(directory_id, note_id)`` tuples,
    * inserts are idempotent via the same ``on_conflict`` flag the
      production repo passes, and
    * the ``fetch(clause, *args, select=...)`` parser does a tiny
      ``WHERE directory_id = $1`` match to keep the helper usable
      from the production code path without spinning up Postgres.

    Tests can populate rows up front via the ``rows`` attribute or
    by calling :meth:`add_note_child`.
    """

    name = "note.directory_note"

    def __init__(self) -> None:
        # each entry: (directory_id, note_id)
        self.rows: set[tuple[str, str]] = set()
        self._id_counter = 0

    def get_id_fields(self) -> List[str]:
        return ["id"]

    async def insert(
        self,
        where: Dict[str, Any],
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[List[Any]]:
        directory_id = where.get("directory_id")
        note_id = where.get("note_id")
        self._id_counter += 1
        self.rows.add((directory_id, note_id))
        return [
            {
                "id": self._id_counter,
                "directory_id": directory_id,
                "note_id": note_id,
            }
        ]

    async def update(self, set, where, returning: str = "*"):
        return []

    async def delete(
        self,
        where: Dict[str, Any],
        returning: str = "*",
    ) -> Optional[List[Any]]:
        directory_id = where.get("directory_id")
        note_id = where.get("note_id")
        before = len(self.rows)
        self.rows = {
            row
            for row in self.rows
            if not (
                (directory_id is None or row[0] == directory_id)
                and (note_id is None or row[1] == note_id)
            )
        }
        after = len(self.rows)
        return [] if before == after else [{"removed": True}]

    async def select(self, where=None, order_by=None, select: str = "*", additional_values=None):
        return list(self.rows)

    async def select_row(self, where, select: str = "*"):
        return None

    async def delete_by_id(self, *id_values: Any):
        return None

    async def fetch_by_id(self, *id_values: Any, select: str = "*"):
        return None

    async def fetch(self, clause: str, *args, **kwargs):
        matches = list(self.rows)
        if args:
            directory_id = args[0]
            matches = [r for r in matches if r[0] == directory_id]
        return [
            {
                "directory_id": parent,
                "note_id": note_id,
            }
            for parent, note_id in matches
        ]

    async def execute(self, sql: str, *args):
        return await self.fetch(sql, *args)

    def add_note_child(self, parent_id: str, note_id: str) -> None:
        """Seed a ``directory_id -> note_id`` row."""
        self.rows.add((parent_id, note_id))


class _FakeDirectoryTable(TableABC):
    """In-memory implementation of the ``note.directory`` table.

    Stores rows by id and lets the
    :class:`~src.db.repos.directory.postgres.PostgresDirectoryRepo`
    exercise its read paths without a real database.  Mirrors the
    production column set the repo callers depend on.
    """

    name = "note.directory"

    def __init__(self) -> None:
        # id -> record dict
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._next_id = 0

    def get_id_fields(self) -> List[str]:
        return ["id"]

    async def insert(self, where, returning: str = "*", on_conflict: str = ""):
        self._next_id += 1
        new_id = f"dir-{self._next_id}"
        record = {"id": new_id, **where}
        self._rows[new_id] = record
        return [_project(record, returning)]

    async def update(self, set, where, returning: str = "*"):
        target_id = where.get("id")
        existing = self._rows.get(target_id) if target_id else None
        if existing is None:
            return []
        existing.update(set)
        return [_project(existing, returning)]

    async def delete(self, where, returning: str = "*"):
        target_id = where.get("id")
        if target_id is None or target_id not in self._rows:
            return []
        removed = self._rows.pop(target_id)
        return [_project(removed, returning)] if returning else []

    async def select(self, where=None, order_by=None, select: str = "*", additional_values=None):
        target_id = (where or {}).get("id")
        if target_id is None:
            return list(self._rows.values())
        return [self._rows[target_id]] if target_id in self._rows else []

    async def select_row(self, where, select: str = "*"):
        results = await self.select(where=where, select=select)
        return results[0] if results else None

    async def delete_by_id(self, *id_values):
        if not id_values:
            return None
        return self._rows.pop(id_values[0], None)

    async def fetch_by_id(self, *id_values, select: str = "*"):
        if not id_values:
            return None
        record = self._rows.get(id_values[0])
        return _project(record, select) if record else None

    async def fetch(self, clause: str, *args, **kwargs):
        # limited support: clause of the form "WHERE id = ANY($1)"
        if clause.strip().startswith("WHERE id = ANY"):
            ids = args[0] if args else []
            return [
                _project(rec, kwargs.get("select", "*"))
                for rec in self._rows.values()
                if rec.get("id") in ids
            ]
        return []

    async def execute(self, sql: str, *args):
        return await self.fetch(sql, *args)

    def add(self, directory_id: str, **fields: Any) -> None:
        """Seed a directory row keyed by ``directory_id``."""
        self._rows[str(directory_id)] = {"id": str(directory_id), **fields}


class _FakeDirectoryTagsTable(TableABC):
    """In-memory implementation of the ``note.directory_tag`` table.

    Retained as a building block for tests that exercise the table
    shape directly.  Production code that touches the
    ``note.directory_tag`` rows now goes through
    :class:`src.api.repos.tag_repo.TagRepoABC`; this fake is still
    here so tests that need a populated association set can
    pre-fill ``rows`` and call :meth:`add_tag`.
    """

    name = "note.directory_tag"

    def __init__(self) -> None:
        # each entry: (directory_id, tag_id)
        self.rows: set[tuple[str, str]] = set()

    def get_id_fields(self) -> List[str]:
        return ["directory_id", "tag_id"]

    def add_tag(self, directory_id: str, tag_id: str) -> None:
        """Seed a ``directory_id -> tag_id`` row."""
        self.rows.add((str(directory_id), str(tag_id)))

    async def insert(
        self,
        where: Dict[str, Any],
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[List[Any]]:
        directory_id = str(where.get("directory_id"))
        tag_id = str(where.get("tag_id"))
        if (directory_id, tag_id) not in self.rows:
            self.rows.add((directory_id, tag_id))
            return [{"directory_id": directory_id, "tag_id": tag_id}]
        return [{"directory_id": directory_id, "tag_id": tag_id}]

    async def update(self, set, where, returning: str = "*"):
        return []

    async def delete(
        self,
        where: Dict[str, Any],
        returning: str = "*",
    ) -> Optional[List[Any]]:
        directory_id = where.get("directory_id")
        tag_id = where.get("tag_id")
        before = len(self.rows)
        self.rows = {
            row
            for row in self.rows
            if not (
                (directory_id is None or row[0] == directory_id)
                and (tag_id is None or row[1] == tag_id)
            )
        }
        return [] if before == len(self.rows) else [{"removed": True}]

    async def select(self, where=None, order_by=None, select: str = "*", additional_values=None):
        items = []
        directory_id = (where or {}).get("directory_id")
        for did, tid in sorted(self.rows):
            if directory_id is not None and did != directory_id:
                continue
            items.append({"directory_id": did, "tag_id": tid})
        return items

    async def select_row(self, where, select: str = "*"):
        results = await self.select(where=where, select=select)
        return results[0] if results else None

    async def delete_by_id(self, *id_values: Any):
        if len(id_values) >= 2:
            self.rows.discard((str(id_values[0]), str(id_values[1])))
        return None

    async def fetch_by_id(self, *id_values: Any, select: str = "*"):
        return None

    async def fetch(self, clause: str, *args, **kwargs):
        return []

    async def execute(self, sql: str, *args):
        return []


def _project(record: Dict[str, Any], returning: str) -> Dict[str, Any]:
    """Mirror the ``RETURNING`` clause on inserts / updates."""
    if not returning or returning.strip() == "*":
        return dict(record)
    columns = [c.strip() for c in returning.split(",")]
    return {col: record.get(col) for col in columns}


class _StubNoteService(NoteServiceABC):
    """In-memory :class:`NoteServiceABC` used by directory-service tests.

    Records every call for assertions, implements the small subset
    :class:`DirectoryServiceImpl` actually uses (``delete_note``,
    ``update_note``), and turns every other call into a clear
    ``NotImplementedError`` so accidental fallthroughs surface
    immediately.
    """

    def __init__(self) -> None:
        self.delete_calls: List[str] = []
        self.update_calls: List[NoteEntity] = []

    async def get_note(
        self, note_id: str, user_ctx: UserContextABC
    ) -> NoteResponse:  # pragma: no cover - unused by directory tests
        raise NotImplementedError

    async def insert_note(
        self, note: NoteEntity, user_ctx: UserContextABC
    ) -> NoteEntity:  # pragma: no cover - unused by directory tests
        raise NotImplementedError

    async def update_note(
        self, note: NoteEntity, user_ctx: UserContextABC
    ) -> NoteEntity:
        self.update_calls.append(note)
        return note

    async def delete_note(
        self, note_id: str, user_ctx: UserContextABC
    ) -> Optional[NoteEntity]:
        self.delete_calls.append(str(note_id))
        return None

    async def search_notes(
        self,
        search_type: str,
        query: str,
        user_ctx: UserContextABC,
        limit: int,
        offset: int,
    ) -> List[NoteEntity]:  # pragma: no cover - unused
        raise NotImplementedError

    async def get_notes(
        self,
        note_ids: List[str],
        user_ctx: UserContextABC,
        options=None,
    ) -> List[NoteEntity]:  # pragma: no cover - unused
        raise NotImplementedError


class _StubAttachmentFacade(AttachmentFacadeABC):
    """In-memory :class:`AttachmentFacadeABC` for directory-service tests.

    Implements only the operations the directory service calls
    (``get_metadata``, ``delete_attachment``,
    ``link_attachment_to_note``); every other method raises so
    accidental callers fail loud.
    """

    def __init__(self) -> None:
        self.metadata_by_key: Dict[str, Attachment] = {}
        self.deleted: List[str] = []
        self.links: List[tuple[str, str]] = []

    def seed_metadata(self, attachment: Attachment) -> None:
        """Pre-populate metadata for ``get_metadata`` lookups."""
        self.metadata_by_key[str(attachment.key)] = attachment

    async def post_attachment(
        self, attachment: Attachment, user_ctx: UserContextABC
    ) -> Attachment:  # pragma: no cover - unused
        raise NotImplementedError

    async def update_metadata(
        self, attachment: Attachment, user_ctx: UserContextABC
    ) -> Attachment:  # pragma: no cover - unused
        raise NotImplementedError

    async def get_attachment(
        self, key: str, user_ctx: UserContextABC
    ) -> Attachment:  # pragma: no cover - unused
        raise NotImplementedError

    async def get_metadata(
        self, key: str, user_ctx: UserContextABC
    ) -> Attachment:
        return self.metadata_by_key[str(key)]

    async def delete_attachment(self, key: str, user_ctx: UserContextABC) -> None:
        self.deleted.append(str(key))

    async def link_attachment_to_note(
        self,
        attachment_key: str,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        self.links.append((str(attachment_key), str(note_id)))

    async def unlink_attachment_from_note(
        self,
        attachment_key: str,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    async def list_attachments_for_note(
        self, note_id: str, user_ctx: UserContextABC
    ) -> List[Attachment]:  # pragma: no cover - unused
        raise NotImplementedError


class _FakeCombinedNoteRepo(CombinedNoteRepoABC):
    """In-memory :class:`CombinedNoteRepoABC` used by pure unit tests.

    Delegates row reads to an optional :class:`_FakeNoteContentRepo`
    so the contents stay in sync; tracks directory / tag id sets in
    separate dicts so the facade's CRUD passes can drive them
    directly.
    """

    def __init__(
        self,
        content_repo: Optional["_FakeNoteContentRepo"] = None,
    ) -> None:
        self._content_repo = content_repo
        self.directory_ids_by_note: Dict[str, List[str]] = {}
        self.tag_ids_by_note: Dict[str, List[str]] = {}

    async def select_by_id(
        self,
        note_id: str,
        *,
        include: Optional["NoteIncludeOptions"] = None,
    ) -> Optional[NoteEntity]:
        if self._content_repo is None:
            raise RuntimeError(
                "_FakeCombinedNoteRepo has no content_repo wired"
            )
        try:
            note = await self._content_repo.select_by_id(note_id)
        except RuntimeError:
            return None
        return self._decorate(note)

    async def select_by_ids(
        self,
        note_ids: List[str],
        *,
        include: Optional["NoteIncludeOptions"] = None,
    ) -> List[NoteEntity]:
        if not note_ids:
            raise ValueError("note_ids must not be empty")
        if self._content_repo is None:
            raise RuntimeError(
                "_FakeCombinedNoteRepo has no content_repo wired"
            )
        try:
            notes = await self._content_repo.select_by_ids(note_ids)
        except ValueError:
            raise
        return [self._decorate(n) for n in notes]

    def _decorate(self, note: NoteEntity) -> NoteEntity:
        if str(note.note_id) in self.directory_ids_by_note:
            note.directory_ids = list(
                self.directory_ids_by_note[str(note.note_id)]
            )
        else:
            note.directory_ids = []
        if str(note.note_id) in self.tag_ids_by_note:
            note.tag_ids = list(self.tag_ids_by_note[str(note.note_id)])
        else:
            note.tag_ids = []
        return note


class _FakeTagRepo(TagRepoABC):
    """In-memory :class:`TagRepoABC` used by pure unit tests.

    Stores the tag taxonomy (id -> slug) and the association
    tables (`note.subject_id -> set(tag_ids)` and
    `directory.subject_id -> set(tag_ids)`).  Exists-and-existence
    checks are answered against this in-memory store so the
    production-side validation logic is exercised end-to-end.
    """

    def __init__(self) -> None:
        # id -> slug
        self.tags_by_id: Dict[str, TagEntity] = {}
        # (subject_type, subject_id) -> set(tag_ids)
        self.bindings: Dict[Tuple[str, str], set] = {}
        # Recorded calls for assertions.
        self.assign_calls: List[tuple[str, str, str]] = []
        self.replace_calls: List[tuple[str, List[str], List[str]]] = []
        self.remove_calls: List[tuple[str, str, str]] = []
        self._next_tag_id = 0

    # ---- tag CRUD ------------------------------------------------------

    async def create_tag(
        self,
        slug: str,
        display_name: str,
    ) -> TagEntity:
        if not slug:
            raise ValueError("slug is required")
        if not display_name:
            raise ValueError("display_name is required")
        for existing in self.tags_by_id.values():
            if existing.slug == slug:
                raise ValueError(f"Tag with slug {slug!r} already exists")
        self._next_tag_id += 1
        tag_id = f"tag-{self._next_tag_id}"
        entity = TagEntity(id=tag_id, slug=slug, display_name=display_name)
        self.tags_by_id[tag_id] = entity
        return entity

    async def get_tag_by_id(self, tag_id: str) -> Optional[TagEntity]:
        return self.tags_by_id.get(str(tag_id))

    async def list_tags(self) -> List[TagEntity]:
        return sorted(
            self.tags_by_id.values(), key=lambda t: t.slug or "",
        )

    async def update_tag(
        self,
        tag_id: str,
        *,
        slug: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Optional[TagEntity]:
        existing = self.tags_by_id.get(str(tag_id))
        if not existing:
            return None
        updated = TagEntity(
            id=existing.id,
            slug=str(slug) if slug is not None else existing.slug,
            display_name=(
                str(display_name) if display_name is not None
                else existing.display_name
            ),
        )
        self.tags_by_id[str(tag_id)] = updated
        return updated

    async def delete_tag(self, tag_id: str) -> bool:
        if not tag_id:
            raise ValueError("tag_id is required")
        removed = self.tags_by_id.pop(str(tag_id), None)
        if removed is None:
            return False
        for key in list(self.bindings.keys()):
            self.bindings[key].discard(str(tag_id))
        return True

    # ---- tag associations ---------------------------------------------

    async def list_tags_for(
        self,
        subject_type: TagSubjectType,
        subject_ids: List[str],
    ) -> Dict[str, List[str]]:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_ids:
            raise ValueError("subject_ids is required")
        cleaned = [str(i) for i in subject_ids if i]
        if not cleaned:
            raise ValueError("subject_ids is required")
        return {
            sid: sorted(self.bindings.get((subject_type, sid), set()))
            for sid in cleaned
        }

    async def assign_tag_to(
        self,
        subject_type: TagSubjectType,
        subject_id: str,
        tag_id: str,
    ) -> None:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_id:
            raise ValueError("subject_id is required")
        if not tag_id:
            raise ValueError("tag_id is required")
        if str(tag_id) not in self.tags_by_id:
            raise ValueError(f"Tag {tag_id!r} does not exist")
        # Note: the production repo also verifies the subject
        # exists in `note.content` / `note.directory`; the in-memory
        # fake is the source of truth here, so any non-empty
        # subject_id is accepted.
        self.assign_calls.append((subject_type, str(subject_id), str(tag_id)))
        self.bindings.setdefault(
            (subject_type, str(subject_id)), set(),
        ).add(str(tag_id))

    async def replace_tags_for(
        self,
        subject_type: TagSubjectType,
        subject_ids: List[str],
        tag_ids: List[str],
    ) -> None:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_ids:
            raise ValueError("subject_ids is required")
        cleaned_subject_ids = [str(i) for i in subject_ids if i]
        if not cleaned_subject_ids:
            raise ValueError("subject_ids is required")
        cleaned_tag_ids = [str(t) for t in tag_ids if t]
        for tag_id in cleaned_tag_ids:
            if tag_id not in self.tags_by_id:
                raise ValueError(f"Tag {tag_id!r} does not exist")

        for sid in cleaned_subject_ids:
            current = self.bindings.get((subject_type, sid), set()).copy()
            desired = set(cleaned_tag_ids)
            self.bindings[(subject_type, sid)] = desired
            self.replace_calls.append((subject_type, sid, list(cleaned_tag_ids)))

    async def remove_tag_from(
        self,
        subject_type: TagSubjectType,
        subject_id: str,
        tag_id: str,
    ) -> None:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_id:
            raise ValueError("subject_id is required")
        if not tag_id:
            raise ValueError("tag_id is required")
        self.remove_calls.append((subject_type, str(subject_id), str(tag_id)))
        bucket = self.bindings.get((subject_type, str(subject_id)))
        if bucket is not None:
            bucket.discard(str(tag_id))


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
    "_FakeDirectorySubdirectoryTable",
    "_FakeDirectoryNoteTable",
    "_FakeDirectoryTable",
    "_FakeCombinedNoteRepo",
    "_FakeNoteTagRepo",
    "_FakeTagRepo",
    "_StubNoteService",
    "_StubAttachmentFacade",
]
