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
from pathlib import Path
from typing import AsyncIterator

import pytest
from authzed.api.v1 import AsyncClient, WriteSchemaRequest
from grpcutil import insecure_bearer_token_credentials
from testcontainers.postgres import PostgresContainer
from testcontainers_spicedb import SpiceDBContainer

from src.db.entities.user.user import UserEntity
from src.db.migrations.context import MigrationContext
from src.db.migrations.runner import MigrationRunner
from src.db.repos import Database
from src.db.repos.directory.directory import DirectoryRepoSpicedbPostgres
from src.db.repos.note.note import UserContext
from src.db.repos.note.permission import NotePermissionRepoSpicedb
from src.db.repos.user.user import UserPostgresRepo
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


@pytest.fixture(scope="function")
async def user_service_env() -> AsyncIterator[tuple[UserServiceRepo, DirectoryRepoSpicedbPostgres]]:
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
        user_repo = UserPostgresRepo(db=db)
        user_service = UserServiceRepo(user_repo=user_repo, directory_repo=directory_repo)

        yield user_service, directory_repo

        await db.close()


async def test_create_user_bootstraps_default_directories_with_real_postgres_and_spicedb(
    user_service_env: tuple[UserServiceRepo, DirectoryRepoSpicedbPostgres]
) -> None:
    """Verify user bootstrap behavior with real Postgres and SpiceDB.

    Assertions validate that:
    - user creation succeeds,
    - exactly the default directories are created,
    - each directory metadata matches the configured default spec,
    - admin permissions are attached for the creating user.
    """
    user_service, directory_repo = user_service_env

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
        assert directory.display_name == spec.display_name
        assert directory.description == spec.description
        assert isinstance(directory.relations, list)
        assert any(
            rel.relation == "admin"
            and str(rel.subject.object_type) == "user"
            and str(rel.subject.object_id) == str(created_user.id)
            for rel in directory.relations
        )
