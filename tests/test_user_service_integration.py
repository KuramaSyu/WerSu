"""Integration test coverage for `UserServiceRepo` with real infrastructure.

This module intentionally uses real Postgres and SpiceDB containers to validate
the full runtime path for user creation bootstrap behavior:

1. user is persisted in Postgres,
2. default zettelkasten directories are created in Postgres,
3. directory permission relationships are written/read via SpiceDB.

These tests are marked as `integration` and `spicedb` and are excluded from the
default test run configured in `pytest.ini`.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import pytest
from authzed.api.v1 import AsyncClient, WriteSchemaRequest
from grpcutil import insecure_bearer_token_credentials
from testcontainers.postgres import PostgresContainer
from testcontainers_spicedb import SpiceDBContainer

from src.api.undefined import UNDEFINED
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.user.user import UserEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.migrations.context import MigrationContext
from src.db.migrations.runner import MigrationRunner
from src.db.repos import Database
from src.db.repos.directory.directory import DirectoryRepoSpicedbPostgres
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.db.repos.note.note import NoteRepoFacade
from src.db.repos.note.note import UserContext
from src.db.repos.note.permission import (
    DirectoryRelationEnum,
    NotePermissionRepoSpicedb,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.db.repos.user.user import UserPostgresRepo
from src.db.table import Table
from src.services.user import UserServiceRepo
from src.utils import logging_provider


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]

SPICEDB_IMAGE = "authzed/spicedb:v1.47.1"
POSTGRES_IMAGE = "pgvector/pgvector:0.8.1-pg18-trixie"


def _create_postgres_dsn(postgres_container: PostgresContainer) -> str:
    """Build a PostgreSQL DSN for a running Postgres test container."""
    return (
        f"postgresql://"
        f"{postgres_container.username}:"
        f"{postgres_container.password}@"
        f"{postgres_container.get_container_host_ip()}:"
        f"{postgres_container.get_exposed_port(5432)}/"
        f"{postgres_container.dbname}"
    )


def _load_spicedb_schema() -> str:
    """Load the canonical SpiceDB schema from the migrations directory."""
    schema_path = Path(__file__).resolve().parents[1] / "src" / "db" / "migrations" / "schema.zed"
    return schema_path.read_text(encoding="utf-8")


async def _wait_until_spicedb_ready(client: AsyncClient, schema: str) -> None:
    """Block until SpiceDB accepts schema writes.

    Parameters
    ----------
    client : AsyncClient
        SpiceDB API client.
    schema : str
        Zed schema text to submit as a readiness probe.

    Raises
    ------
    RuntimeError
        If SpiceDB does not become ready within the retry budget.
    """
    attempts = 30
    retry_delay_s = 0.5
    last_error: Exception | None = None

    for _ in range(attempts):
        try:
            await client.WriteSchema(WriteSchemaRequest(schema=schema))
            return
        except Exception as exc:  # pragma: no cover - startup race only
            last_error = exc
            await asyncio.sleep(retry_delay_s)

    raise RuntimeError("SpiceDB container did not become ready in time") from last_error


async def _wait_until(
    condition,
    *,
    timeout_s: float = 5.0,
    interval_s: float = 0.1,
) -> None:
    """Wait until an async condition returns True."""
    attempts = max(1, int(timeout_s / interval_s))
    for _ in range(attempts):
        if await condition():
            return
        await asyncio.sleep(interval_s)
    raise AssertionError("Condition was not met within timeout")


class _StubEmbeddingRepo(NoteEmbeddingRepo):
    async def insert(self, note_id: str, title: str, content: str):
        raise RuntimeError("_StubEmbeddingRepo.insert should not be called in this integration test")

    async def update(self, set, where):
        raise NotImplementedError

    async def delete(self, embedding):
        raise NotImplementedError

    async def select(self, embedding):
        return []

    @property
    def embedding_generator(self):
        class _Generator:
            @property
            def model_name(self) -> str:
                return "stub"

            def generate(self, text: str):
                return []

        return _Generator()


@pytest.fixture(scope="function")
async def user_service_env() -> AsyncIterator[
    tuple[UserServiceRepo, DirectoryRepoSpicedbPostgres, NoteRepoFacade, NotePermissionRepoSpicedb]
]:
    """Provision a real `UserServiceRepo` integration environment.

    The fixture starts both testcontainers, runs DB and SpiceDB migrations,
    then yields fully wired concrete repositories and service instances.

    Yields
    ------
    tuple[UserServiceRepo, DirectoryRepoSpicedbPostgres]
        Service under test and directory repo used for post-assertions.
    """
    with PostgresContainer(
        image=POSTGRES_IMAGE,
        username="postgres",
        password="postgres",
        dbname="testdb",
    ) as postgres, SpiceDBContainer(image=SPICEDB_IMAGE) as spicedb:
        db = Database(_create_postgres_dsn(postgres), logging_provider, init_file="src/init.sql")
        await db.init_db()

        spicedb_client = AsyncClient(
            spicedb.get_endpoint(),
            insecure_bearer_token_credentials(spicedb.get_secret_key()),
        )
        await _wait_until_spicedb_ready(spicedb_client, _load_spicedb_schema())

        migration_runner = MigrationRunner(
            ctx=MigrationContext(db=db, spicedb_client=spicedb_client),
            log_provider=logging_provider,
        )
        await migration_runner.run_pending_migrations()

        permission_repo = NotePermissionRepoSpicedb(client=spicedb_client)
        directory_repo = DirectoryRepoSpicedbPostgres(
            db=db,
            permission_repo=permission_repo,
            spicedb_client=spicedb_client,
        )
        content_table = Table(
            db=db,
            table_name="note.content",
            id_fields=["id"],
            logging_provider=logging_provider,
        )
        note_repo = NoteRepoFacade(
            db=db,
            content_repo=NoteContentPostgresRepo(content_table),
            embedding_repo=_StubEmbeddingRepo(),
            permission_repo=permission_repo,
            directory_repo=directory_repo,
            logging_provider=logging_provider,
        )
        user_repo = UserPostgresRepo(db=db)
        user_service = UserServiceRepo(user_repo=user_repo, directory_repo=directory_repo)

        yield user_service, directory_repo, note_repo, permission_repo

        await db.close()


async def test_create_user_bootstraps_default_directories_with_real_postgres_and_spicedb(
    user_service_env: tuple[UserServiceRepo, DirectoryRepoSpicedbPostgres, NoteRepoFacade, NotePermissionRepoSpicedb]
) -> None:
    """Verify user bootstrap behavior with real Postgres and SpiceDB.

    Assertions validate that:
    - user creation succeeds,
    - exactly the default directories are created,
    - each directory metadata matches the configured default spec,
    - admin permissions are attached for the creating user.
    """
    user_service, directory_repo, _, permission_repo = user_service_env

    created_user = await user_service.create_user(
        UserEntity(
            discord_id=1357913579,
            avatar="https://cdn.example/avatar.png",
            username="integration-user",
            discriminator="4321",
            email="integration@example.com",
        )
    )

    assert created_user.id is not None

    fetched_user = await user_service.get_user(user_id=created_user.id)
    assert fetched_user is not None
    assert fetched_user.id == created_user.id

    directory_ids = await directory_repo.list_user_directory_ids(UserContext(created_user.id))
    assert len(directory_ids) == 3

    directories = [await directory_repo.fetch_directory(directory_id) for directory_id in directory_ids]
    directories = [directory for directory in directories if directory is not None]
    assert len(directories) == 3

    default_specs = directory_repo.get_default_directory_specs()
    by_name = {directory.name: directory for directory in directories}

    for spec in default_specs:
        assert spec.name in by_name
        directory = by_name[spec.name]
        assert directory.id is not None
        assert directory.display_name == spec.display_name
        assert directory.description == spec.description
        assert isinstance(directory.relations, list)
        assert any(
            str(rel.relation) == DirectoryRelationEnum.ADMIN.value
            and str(rel.subject.object_type) == ObjectTypeEnum.USER.value
            and str(rel.subject.object_id) == str(created_user.id)
            for rel in directory.relations
        )

        # check computed permissions via SpiceDB schema are working for admin relation
        resource = ObjectRef(object_type=ObjectTypeEnum.DIRECTORY, object_id=str(directory.id))
        async def _can_view() -> bool:
            return await permission_repo.has_permission(UserContext(created_user.id), "view", resource)

        await _wait_until(_can_view)
        assert await permission_repo.has_permission(UserContext(created_user.id), "view", resource)
        assert await permission_repo.has_permission(UserContext(created_user.id), "write", resource)
        assert await permission_repo.has_permission(UserContext(created_user.id), "delete", resource)
        assert not await permission_repo.has_permission(UserContext("another-user"), "view", resource)


async def test_insert_note_uses_default_fleeting_directory_when_parent_not_specified(
    user_service_env: tuple[UserServiceRepo, DirectoryRepoSpicedbPostgres, NoteRepoFacade, NotePermissionRepoSpicedb]
) -> None:
    user_service, directory_repo, note_repo, permission_repo = user_service_env

    created_user = await user_service.create_user(
        UserEntity(
            discord_id=2468024680,
            avatar="https://cdn.example/avatar-2.png",
            username="integration-user-2",
            discriminator="2222",
            email="integration2@example.com",
        )
    )
    assert created_user.id is not None

    directory_ids = await directory_repo.list_user_directory_ids(UserContext(created_user.id))
    directories = [await directory_repo.fetch_directory(directory_id) for directory_id in directory_ids]
    directories = [directory for directory in directories if directory is not None]
    default_name = directory_repo.get_default_directory_specs()[0].name
    default_dirs = [directory for directory in directories if directory.name == default_name]
    assert len(default_dirs) == 1
    default_directory = default_dirs[0]
    assert default_directory.id is not None

    note = await note_repo.insert(
        NoteEntity(
            title="No explicit parent",
            content="",
            updated_at=datetime.now(),
            author_id=created_user.id,
        ),
        UserContext(created_user.id),
    )
    assert note.note_id is not None

    relationships = await permission_repo.list_relationships(
        ObjectRef(object_type=ObjectTypeEnum.NOTE, object_id=str(note.note_id))
    )

    async def _has_default_parent_relation() -> bool:
        rels = await permission_repo.list_relationships(
            ObjectRef(object_type=ObjectTypeEnum.NOTE, object_id=str(note.note_id))
        )
        return any(
            str(rel.relation) == NoteRelationEnum.PARENT_DIRECTORY.value
            and str(rel.subject.object_id) == str(default_directory.id)
            for rel in rels
        )

    await _wait_until(_has_default_parent_relation)
    assert await _has_default_parent_relation()


async def test_insert_note_uses_specified_parent_directory_when_provided(
    user_service_env: tuple[UserServiceRepo, DirectoryRepoSpicedbPostgres, NoteRepoFacade, NotePermissionRepoSpicedb]
) -> None:
    user_service, directory_repo, note_repo, permission_repo = user_service_env

    created_user = await user_service.create_user(
        UserEntity(
            discord_id=1122334455,
            avatar="https://cdn.example/avatar-3.png",
            username="integration-user-3",
            discriminator="3333",
            email="integration3@example.com",
        )
    )
    assert created_user.id is not None

    custom_directory = await directory_repo.create_directory(
        DirectoryEntity(
            name="project_notes",
            display_name="Project Notes",
            description="Custom parent directory for explicit note placement.",
            relations=[
                Relationship(
                    resource=ObjectRef(object_type=ObjectTypeEnum.DIRECTORY, object_id=UNDEFINED),
                    relation=DirectoryRelationEnum.ADMIN,
                    subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id=str(created_user.id)),
                )
            ],
        )
    )
    assert custom_directory.id is not None

    note = await note_repo.insert(
        NoteEntity(
            title="Explicit parent",
            content="",
            updated_at=datetime.now(),
            author_id=created_user.id,
            parent_dir_id=str(custom_directory.id),
        ),
        UserContext(created_user.id),
    )
    assert note.note_id is not None

    relationships = await permission_repo.list_relationships(
        ObjectRef(object_type=ObjectTypeEnum.NOTE, object_id=str(note.note_id))
    )

    async def _has_custom_parent_relation() -> bool:
        rels = await permission_repo.list_relationships(
            ObjectRef(object_type=ObjectTypeEnum.NOTE, object_id=str(note.note_id))
        )
        return any(
            str(rel.relation) == NoteRelationEnum.PARENT_DIRECTORY.value
            and str(rel.subject.object_id) == str(custom_directory.id)
            for rel in rels
        )

    await _wait_until(_has_custom_parent_relation)
    assert await _has_custom_parent_relation()
