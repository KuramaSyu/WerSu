"""Shared helpers and fixtures for integration tests.

This module centralizes the boilerplate required to spin up real
Postgres and SpiceDB containers so individual integration tests can stay
focused on the behavior under test.

Reuse
-----
Import the fixtures from a test module (``from tests.integration_helpers
import user_service_env``) and pytest will discover them automatically.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Tuple

import pytest
from authzed.api.v1 import AsyncClient, WriteSchemaRequest
from grpcutil import insecure_bearer_token_credentials
from testcontainers.postgres import PostgresContainer
from testcontainers_spicedb import SpiceDBContainer

from src.api.undefined import UNDEFINED
from src.api import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.user.user import UserEntity
from src.db.migrations.context import MigrationContext
from src.db.migrations.runner import MigrationRunner
from src.db.repos import Database
from src.db.repos.directory.directory import DirectoryRepoSpicedbPostgres
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.note import NoteRepoFacade, UserContext
from src.db.repos.permissions.permission import NotePermissionRepoSpicedb
from src.db.repos.sharing.sharing import SharingPostgresRepo
from src.db.repos.user.user import UserPostgresRepo
from src.db.table import Table
from src.services.permissions import PermissionServiceRepo
from src.services.sharing import DefaultSharingService
from src.services.user import UserService
from src.utils import logging_provider
from tests.fixtures import _FakeEmbeddingRepo


POSTGRES_IMAGE = "pgvector/pgvector:0.8.1-pg18-trixie"
SPICEDB_IMAGE = "authzed/spicedb:v1.47.1"

_REPO_ROOT = Path(__file__).resolve().parents[1]
SPICEDB_SCHEMA_PATH = _REPO_ROOT / "src" / "db" / "migrations" / "schema.zed"


def postgres_dsn(postgres_container: PostgresContainer) -> str:
    """Build a PostgreSQL DSN for a running testcontainer."""
    return (
        f"postgresql://"
        f"{postgres_container.username}:"
        f"{postgres_container.password}@"
        f"{postgres_container.get_container_host_ip()}:"
        f"{postgres_container.get_exposed_port(5432)}/"
        f"{postgres_container.dbname}"
    )


def load_spicedb_schema() -> str:
    """Read the canonical SpiceDB schema from the migrations directory."""
    return SPICEDB_SCHEMA_PATH.read_text(encoding="utf-8")


async def wait_until_spicedb_ready(client: AsyncClient, schema: str) -> None:
    """Block until SpiceDB accepts a schema write."""
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


async def wait_until(
    condition,
    *,
    timeout_s: float = 5.0,
    interval_s: float = 0.1,
    description: str = "condition",
) -> None:
    """Wait until an async condition returns True.

    SpiceDB writes are eventually consistent; this helper makes the
    poll-and-retry mechanics reusable across tests instead of being
    reimplemented inline.

    Parameters
    ----------
    condition
        Zero-arg awaitable returning a truthy value when the wait is over.
    timeout_s
        Total time to keep polling before giving up.
    interval_s
        Sleep between polls.
    description
        Human-readable label for the condition; used in the failure
        message so test output explains what never became true.
    """
    attempts = max(1, int(timeout_s / interval_s))
    for _ in range(attempts):
        if await condition():
            return
        await asyncio.sleep(interval_s)
    pytest.fail(
        f"wait_until timed out after {timeout_s}s waiting for {description!r}"
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@dataclass
class IntegrationEnv:
    """Bundle of real-infrastructure collaborators for integration tests.

    Tests are free to grab whichever collaborator they need; unused ones
    are simply ignored.  Sharing the same instance across tests is
    intentional: each fixture invocation still starts fresh containers.
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


@asynccontextmanager
async def _integration_env_ctx() -> AsyncIterator[IntegrationEnv]:
    """Spin up Postgres + SpiceDB containers and wire all collaborators.

    Used by every integration fixture below so the container lifecycle,
    migration runner, and dependency wiring stay consistent.
    """
    with (
        PostgresContainer(
            image=POSTGRES_IMAGE,
            username="postgres",
            password="postgres",
            dbname="testdb",
        ) as postgres,
        SpiceDBContainer(image=SPICEDB_IMAGE) as spicedb,
    ):
        db = Database(postgres_dsn(postgres), logging_provider, init_file="src/init.sql")
        await db.init_db()

        spicedb_client = AsyncClient(
            spicedb.get_endpoint(),
            insecure_bearer_token_credentials(spicedb.get_secret_key()),
        )
        await wait_until_spicedb_ready(spicedb_client, load_spicedb_schema())

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
        sharing_service = DefaultSharingService(
            sharing_repo=sharing_repo,
            user_repo=user_repo,
            permission_repo=permission_repo,
            permission_service=permission_service,
            logging_provider=logging_provider,
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


@pytest.fixture(scope="function")
async def user_service_env() -> AsyncIterator[
    Tuple[
        UserService,
        DirectoryRepoSpicedbPostgres,
        NoteRepoFacade,
        NotePermissionRepoSpicedb,
    ]
]:
    """Provision a real Postgres + SpiceDB environment for user service tests.

    Yields
    ------
    tuple
        ``(user_service, directory_repo, note_repo, permission_repo)`` wired
        against the testcontainers, with all migrations applied.
    """
    async with _integration_env_ctx() as env:
        yield env.user_service, env.directory_repo, env.note_repo, env.permission_repo


@pytest.fixture(scope="function")
async def sharing_service_env() -> AsyncIterator[IntegrationEnv]:
    """Provision a real Postgres + SpiceDB environment for sharing tests.

    Yields
    ------
    IntegrationEnv
        Full bundle of wired collaborators. Tests that need a single
        collaborator can pull it off the dataclass by attribute.
    """
    async with _integration_env_ctx() as env:
        yield env


# ---------------------------------------------------------------------------
# Reusable assertion helpers
# ---------------------------------------------------------------------------

async def assert_user_has_admin_on_directory(
    permission_repo: NotePermissionRepoSpicedb,
    user_id: str,
    directory_id: str,
) -> None:
    """Poll SpiceDB until the user is admin on the directory, then assert.

    SpiceDB's bulk-export view of relationships is eventually consistent
    after ``ImportBulkRelationships`` returns.  Callers should use this
    helper instead of asserting immediately after creating a relation.
    """
    resource = ObjectRef(ObjectTypeEnum.DIRECTORY, str(directory_id))
    actor = UserContext(str(user_id))

    async def _can_admin() -> bool:
        return await permission_repo.has_permission(actor, "delete", resource)

    await wait_until(_can_admin)
    # `delete` is gated behind admin in the schema, so the wait above
    # implicitly verifies the admin tuple was applied.
    assert await permission_repo.has_permission(actor, "view", resource)
    assert await permission_repo.has_permission(actor, "write", resource)
    assert await permission_repo.has_permission(actor, "delete", resource)


def make_user_entity(
    *,
    discord_id: int,
    username: str,
    discriminator: str,
    email: str,
    avatar: str = "https://cdn.example/avatar.png",
) -> UserEntity:
    """Factory for a human ``UserEntity`` with sensible defaults."""
    return UserEntity(
        discord_id=discord_id,
        avatar=avatar,
        username=username,
        discriminator=discriminator,
        email=email,
    )


def make_custom_directory(
    *,
    owner_user_id: str,
    name: str = "project_notes",
    display_name: str = "Project Notes",
    description: str = "Custom parent directory for explicit note placement.",
) -> DirectoryEntity:
    """Factory for a custom directory that grants admin to a user."""
    return DirectoryEntity(
        name=name,
        display_name=display_name,
        description=description,
        relations=[
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, UNDEFINED),
                relation=DirectoryRelationEnum.ADMIN,
                subject=SubjectRef(ObjectTypeEnum.USER, owner_user_id),
            )
        ],
    )


__all__ = [
    # constants
    "POSTGRES_IMAGE",
    "SPICEDB_IMAGE",
    "SPICEDB_SCHEMA_PATH",
    # infrastructure helpers
    "postgres_dsn",
    "load_spicedb_schema",
    "wait_until_spicedb_ready",
    "wait_until",
    # shared dataclass for the integration env
    "IntegrationEnv",
    # fixtures
    "user_service_env",
    "sharing_service_env",
    # assertion + factory helpers
    "assert_user_has_admin_on_directory",
    "make_user_entity",
    "make_custom_directory",
    # re-exports for convenience
    "NoteRelationEnum",
    "ObjectRef",
    "ObjectTypeEnum",
    "Relationship",
    "SubjectRef",
    "UserContext",
]
