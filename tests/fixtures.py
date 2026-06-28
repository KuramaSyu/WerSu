import asyncio
from datetime import datetime
from typing import Any, Iterator, List, Optional
import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer
from src.api.user_context import UserContextABC
from src.ai.embedding_generator import EmbeddingGenerator, Models
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.entities.note.versioning import NoteVersionEntry
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.embedding import NoteEmbeddingPostgresRepo, NoteEmbeddingRepo
from src.db.repos.note.note import NoteRepoFacade, NoteRepoFacadeABC
from src.db.repos.note.versioning import NoteVersionPostgresRepo, NoteVersionRepoABC
from src.db.repos.note.permission import NotePermissionRepoInMemory
from src.db.table import Table
from src.db.entities.user.user import UserEntity
from src.db.migrations.context import MigrationContext
from src.db.migrations.runner import MigrationRunner
from src.db.repos.user.user import UserRepoABC
from src.db.repos import UserPostgresRepo, Database
from src.utils import logging_provider


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
    async def WriteSchema(self, request) -> None:
        return None

def create_postgres_dsn(postgres_container: PostgresContainer) -> str:
    return (
        f"postgresql://"
        f"{postgres_container.username}:"
        f"{postgres_container.password}@"
        f"{postgres_container.get_container_host_ip()}:"
        f"{postgres_container.get_exposed_port(5432)}/"
        f"{postgres_container.dbname}"
    )

@pytest.fixture(scope="session")
def dsn() -> Iterator[str]:
    container = PostgresContainer(
        image="pgvector/pgvector:0.8.1-pg18-trixie",
        username="postgres",
        password="postgres",
        dbname="testdb",
    )
    container.start()
    dsn = create_postgres_dsn(container)
    yield dsn
    container.stop()

@pytest.fixture(scope="function")
def test_user() -> UserEntity:
    return UserEntity(
        discord_id=987654321,
        avatar="sldfjseoisjldkj",
        username="KuramaSyu",
        discriminator="1234",
        email="kuramaSyu@example.com"
    )

@pytest.fixture(scope="function")
async def db(dsn):
    db = Database(dsn, logging_provider, init_file="src/init.sql")
    await db.init_db()

    # apply migrations for test database setup
    migration_runner = MigrationRunner(
        ctx=MigrationContext(
            db=db,
            spicedb_client=_TestSpiceDbClient(),
        ),
        log_provider=logging_provider,
    )
    await migration_runner.run_pending_migrations()

    # clean state
    await db.execute("""
    TRUNCATE TABLE
        users,
        note.attachment,
        note.attachment_note_link,
        note.directory,
        note.content,
        note.version_snapshot,
        note.version_delta
    CASCADE;
    """)

    yield db
    await db.close()

@pytest.fixture(scope="function")
def note_repo_facade(db: Database) -> NoteRepoFacadeABC:
    common_table_kwargs = {"db": db, "logging_provider": logging_provider}
    content_table = Table(
        **common_table_kwargs, 
        table_name="note.content", 
        id_fields=["id"],
        error_log=True
    )
    embedding_table = Table(
        **common_table_kwargs,
        table_name="note.embedding",
        id_fields=["note_id", "model"],
        error_log=True
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
        max_deltas_per_snapshot=2,
    )

    repo = NoteRepoFacade(
        db=db,
        content_repo=NoteContentPostgresRepo(content_table),
        embedding_repo=NoteEmbeddingPostgresRepo(
            table=embedding_table,
            embedding_generator=EmbeddingGenerator(
                model_name=Models.MINI_LM_L6_V2, 
                logging_provider=logging_provider
            )
        ),
        # TODO: testing with SpiceDB could get hard. Maybe make a Fake which does not do any checks 
        permission_repo=NotePermissionRepoInMemory(),
        directory_repo=_TestDirectoryRepo(),
        logging_provider=logging_provider,
        version_repo=version_repo,
    )
    return repo

@pytest.fixture(scope="function")
async def user_repo(db: Database) -> UserRepoABC:
    return UserPostgresRepo(db)

