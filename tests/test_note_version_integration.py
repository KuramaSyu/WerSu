from dataclasses import replace
from datetime import datetime
from typing import List, Optional

from src.api.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.db.repos.note.note import NoteRepoFacade, UserContext
from src.db.repos.note.permission import NotePermissionRepoInMemory
from src.db.repos.note.versioning import NoteVersionPostgresRepo
from src.db.table import Table
from src.utils import logging_provider

from .fixtures import db, dsn, test_user, user_repo


class _TestDirectoryRepo(DirectoryRepo):
    """Minimal directory repo for versioning integration tests."""

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


class _FakeEmbeddingGenerator:
    """Lightweight embedding generator to avoid heavy model loads."""

    @property
    def model_name(self) -> str:
        return "fake"

    def generate(self, text: str):
        return [0.0]

    def tensor_to_sequence(self, tensor):
        return [0.0]


class _FakeEmbeddingRepo(NoteEmbeddingRepo):
    """Stub embedding repo that avoids ML dependencies."""

    def __init__(self) -> None:
        self._generator = _FakeEmbeddingGenerator()

    @property
    def embedding_generator(self):
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


async def test_note_versioning_records_snapshots_and_deltas(db, user_repo, test_user) -> None:
    """Integration test: insert/update note and validate version history."""
    user = await user_repo.insert(test_user)
    ctx = UserContext(user_id=user.id)

    common_table_kwargs = {"db": db, "logging_provider": logging_provider}
    content_table = Table(
        **common_table_kwargs,
        table_name="note.content",
        id_fields=["id"],
        error_log=True,
    )
    version_snapshot_table = Table(
        **common_table_kwargs,
        table_name="note.version_snapshot",
        id_fields=["snapshot_id"],
        error_log=True,
    )
    version_delta_table = Table(
        **common_table_kwargs,
        table_name="note.version_delta",
        id_fields=["delta_id"],
        error_log=True,
    )

    version_repo = NoteVersionPostgresRepo(
        snapshot_table=version_snapshot_table,
        delta_table=version_delta_table,
        max_deltas_per_snapshot=1,
    )

    note_repo = NoteRepoFacade(
        db=db,
        content_repo=NoteContentPostgresRepo(content_table),
        embedding_repo=_FakeEmbeddingRepo(),
        permission_repo=NotePermissionRepoInMemory(),
        directory_repo=_TestDirectoryRepo(),
        logging_provider=logging_provider,
        version_repo=version_repo,
    )

    base_note = NoteEntity(
        title="v1",
        content="alpha",
        updated_at=datetime(2026, 5, 18, 11, 0, 0),
        author_id=user.id,
    )
    created = await note_repo.insert(base_note, ctx)

    updated_v2 = replace(
        created,
        title="v2",
        content="bravo",
        updated_at=datetime(2026, 5, 18, 11, 5, 0),
    )
    await note_repo.update(updated_v2, ctx)

    updated_v3 = replace(
        created,
        title="v3",
        content="charlie",
        updated_at=datetime(2026, 5, 18, 11, 10, 0),
    )
    await note_repo.update(updated_v3, ctx)

    versions = await version_repo.list_versions(created.note_id, limit=10, offset=0)
    assert len(versions) == 3
    assert versions[0].is_snapshot is True
    assert versions[0].version_index == 3

    restored_v2 = await version_repo.get_content_at_version(created.note_id, 2)
    assert restored_v2.title == "v2"
    assert restored_v2.content == "bravo"
