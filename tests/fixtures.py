import asyncio
from typing import Iterator
import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer
from src.ai.embedding_generator import EmbeddingGenerator, Models
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.embedding import NoteEmbeddingPostgresRepo
from src.db.repos.note.note import NoteRepoFacade, NoteRepoFacadeABC
from src.db.repos.note.permission import NotePermissionPostgresRepo
from src.db.table import Table
from src.db.entities.user.user import UserEntity
from src.db.repos.user.user import UserRepoABC
from src.db.repos import UserPostgresRepo, Database
from src.utils import logging_provider

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
        image="pgvector/pgvector:pg16",
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

    # clean state
    await db.execute("""
    TRUNCATE TABLE
        users,
        note.content,
        role.permission,
        role.role,
        role.metadata
    RESTART IDENTITY CASCADE;
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
    permission_table = Table(
        **common_table_kwargs, 
        table_name="note.permission", 
        id_fields=["note_id", "role_id"],
        error_log=True
    )
    embedding_table = Table(
        **common_table_kwargs,
        table_name="note.embedding",
        id_fields=["note_id", "model"],
        error_log=True
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
        permission_repo=NotePermissionPostgresRepo(permission_table),
        logging_provider=logging_provider
    )
    return repo

@pytest.fixture(scope="function")
async def user_repo(db: Database) -> UserRepoABC:
    return UserPostgresRepo(db)

