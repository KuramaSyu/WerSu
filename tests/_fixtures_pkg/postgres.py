"""Combined Postgres + SpiceDB integration environment.

This is the canonical "full stack" fixture used by ``tests/integration_helpers.py``
to wire up the ``IntegrationEnv`` dataclass.  Older tests that called
``user_service_env`` or ``sharing_service_env`` continue to work because
``integration_helpers.py`` re-exports the same names built on top of the
helpers defined here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

import pytest
from authzed.api.v1 import AsyncClient
from testcontainers.postgres import PostgresContainer
from testcontainers_spicedb import SpiceDBContainer

from src.db.migrations.context import MigrationContext
from src.db.migrations.runner import MigrationRunner
from src.db.repos import Database
from src.db.repos.directory.directory import DirectoryRepoSpicedbPostgres
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.note import NoteRepoFacade
from src.db.repos.permissions.permission import NotePermissionRepoSpicedb
from src.db.repos.sharing.sharing import SharingPostgresRepo
from src.db.repos.user.user import UserPostgresRepo
from src.db.repos.user.user_action import UserActionPostgresRepo
from src.db.table import Table
from src.services.permissions import PermissionServiceRepo
from src.services.sharing import DefaultSharingService
from src.services.user import UserService
from src.utils import logging_provider
from tests._fixtures_pkg.fakes import _FakeEmbeddingRepo
from tests._fixtures_pkg.spicedb_schema import (
    SPICEDB_IMAGE,
    create_spicedb_client,
    load_spicedb_schema,
    wait_until_spicedb_ready,
)


# Image constants.  Mirrored here rather than imported from ``spicedb_schema``
# because the postgres image is a concern of *this* module only.
POSTGRES_IMAGE = "pgvector/pgvector:0.8.1-pg18-trixie"


def postgres_dsn(container: PostgresContainer) -> str:
    """Build a PostgreSQL DSN for a running testcontainer."""
    return (
        f"postgresql://{container.username}:{container.password}@"
        f"{container.get_container_host_ip()}:{container.get_exposed_port(5432)}/"
        f"{container.dbname}"
    )


@dataclass
class IntegrationEnv:
    """Bundle of wired collaborators against real Postgres + SpiceDB.

    Concrete instances come from
    :func:`tests.fixtures.postgres.spicedb_postgres_env`.  Tests that
    need a single collaborator pick the relevant attribute; tests that
    need everything grab the whole dataclass.
    """

    db: Database
    spicedb_client: AsyncClient
    permission_repo: NotePermissionRepoSpicedb
    directory_repo: DirectoryRepoSpicedbPostgres
    note_repo: NoteRepoFacade
    user_repo: UserPostgresRepo
    user_service: UserService
    permission_service: PermissionServiceRepo
    sharing_repo: SharingPostgresRepo
    sharing_service: DefaultSharingService


@pytest.fixture(scope="function")
async def spicedb_postgres_env() -> AsyncIterator[IntegrationEnv]:
    """Provision Postgres + SpiceDB and yield wired collaborators."""
    with PostgresContainer(
        image=POSTGRES_IMAGE,
        username="postgres",
        password="postgres",
        dbname="testdb",
    ) as postgres, SpiceDBContainer(image=SPICEDB_IMAGE) as spicedb:
        db = Database(
            postgres_dsn(postgres),
            logging_provider,
            init_file="src/init.sql",
        )
        await db.init_db()

        spicedb_client = create_spicedb_client(
            spicedb.get_endpoint(),
            spicedb.get_secret_key(),
        )
        await wait_until_spicedb_ready(spicedb_client, load_spicedb_schema())

        migration_runner = MigrationRunner(
            ctx=MigrationContext(db=db, spicedb_client=spicedb_client),
            log_provider=logging_provider,
        )
        await migration_runner.run_pending_migrations()

        permission_repo = NotePermissionRepoSpicedb(
            client=spicedb_client,
            consistent=True,
        )
        directory_repo = DirectoryRepoSpicedbPostgres(
            db=db,
            permission_repo=permission_repo,
            spicedb_client=spicedb_client,
        )
        note_repo = NoteRepoFacade(
            db=db,
            content_repo=NoteContentPostgresRepo(
                Table(
                    db=db,
                    table_name="note.content",
                    id_fields=["id"],
                    logging_provider=logging_provider,
                )
            ),
            embedding_repo=_FakeEmbeddingRepo(),
            permission_repo=permission_repo,
            directory_repo=directory_repo,
            logging_provider=logging_provider,
        )
        user_repo = UserPostgresRepo(db=db)
        user_service = UserService(user_repo=user_repo, directory_repo=directory_repo)
        permission_service = PermissionServiceRepo(
            permission_repo=permission_repo,
            note_repo=note_repo,
            directory_repo=directory_repo,
        )
        sharing_repo = SharingPostgresRepo(
            table=Table(
                db=db,
                table_name="shared",
                id_fields=["id"],
                logging_provider=logging_provider,
            ),
            logging_provider=logging_provider,
        )
        user_action_repo = UserActionPostgresRepo(
            table=Table(
                db=db,
                table_name="user_action",
                id_fields=["id"],
                logging_provider=logging_provider,
            ),
            logging_provider=logging_provider,
        )
        sharing_service = DefaultSharingService(
            sharing_repo=sharing_repo,
            user_repo=user_repo,
            permission_repo=permission_repo,
            permission_service=permission_service,
            logging_provider=logging_provider,
            user_action_repo=user_action_repo,
        )

        try:
            yield IntegrationEnv(
                db=db,
                spicedb_client=spicedb_client,
                permission_repo=permission_repo,
                directory_repo=directory_repo,
                note_repo=note_repo,
                user_repo=user_repo,
                user_service=user_service,
                permission_service=permission_service,
                sharing_repo=sharing_repo,
                sharing_service=sharing_service,
            )
        finally:
            await db.close()


__all__ = [
    "POSTGRES_IMAGE",
    "postgres_dsn",
    "IntegrationEnv",
    "spicedb_postgres_env",
]
