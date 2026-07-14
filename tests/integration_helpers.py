"""Backwards-compatible re-export of the integration test fixtures.

The infrastructure-heavy fixtures and helpers previously defined here
have moved into submodules of :mod:`tests.fixtures`:

* :mod:`tests.fixtures.spicedb_schema` -> ``SPICEDB_IMAGE``, ``load_spicedb_schema``,
                                         ``wait_until_spicedb_ready``, ``SPICEDB_SCHEMA_PATH``
* :mod:`tests.fixtures.spicedb`        -> ``spicedb_client``, ``spicedb_permission_repo``,
                                         ``idempotent_permission_repo``
* :mod:`tests.fixtures.postgres`       -> ``IntegrationEnv``, ``spicedb_postgres_env``,
                                         ``postgres_dsn``, ``POSTGRES_IMAGE``
* :mod:`tests.fixtures.garage`         -> ``garage_config``, ``s3_client``, garage constants
* :mod:`tests.fixtures.fakes`          -> in-memory test doubles

This module re-exports those names, plus the integration-specific
helpers (``wait_until``, ``user_service_env``, ``sharing_service_env``,
``IntegrationEnv``, assertion helpers, and entity factories).

New test files should import directly from :mod:`tests.fixtures.*`
when possible; this module exists so existing test files keep working
after the move.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Tuple

import pytest

from src.api.other.undefined import UNDEFINED
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
from src.db.repos.directory.directory import DirectoryFacadeImpl
from src.db.repos.note.note_facade import NoteFacadeImpl
from src.db.repos.permissions.spicedb_repo import SpicedbPermissionRepo
from src.db.repos.user import RepoUserContext
from tests.stubs.user_context import _UserContext as UserContext
from src.services.user_service import UserServiceImpl

# Sub-module exports (re-exported for legacy callers).
from tests._fixtures_pkg.spicedb_schema import (  # noqa: F401
    SPICEDB_IMAGE,
    SPICEDB_SCHEMA_PATH,
    load_spicedb_schema,
    wait_until_spicedb_ready,
)
from tests._fixtures_pkg.spicedb import (  # noqa: F401
    idempotent_permission_repo,
    spicedb_client,
    spicedb_permission_repo,
)
from tests._fixtures_pkg.postgres import (  # noqa: F401
    POSTGRES_IMAGE,
    IntegrationEnv,
    postgres_dsn,
    spicedb_postgres_env,
)
from tests._fixtures_pkg.garage import (  # noqa: F401
    garage_config,
    s3_client,
)


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
    """
    attempts = max(1, int(timeout_s / interval_s))
    for _ in range(attempts):
        if await condition():
            return
        await asyncio.sleep(interval_s)
    pytest.fail(
        f"wait_until timed out after {timeout_s}s waiting for {description!r}"
    )


@pytest.fixture(scope="function")
async def user_service_env(
    spicedb_postgres_env: IntegrationEnv,
) -> Tuple[
    UserServiceImpl,
    DirectoryFacadeImpl,
    NoteFacadeImpl,
    SpicedbPermissionRepo,
]:
    """Provision a real Postgres + SpiceDB environment for user service tests.

    Yields a tuple ``(user_service, directory_repo, note_repo,
    permission_repo)`` so existing tests that destructure this exact
    order keep working unchanged.
    """
    env = spicedb_postgres_env
    return (
        env.user_service,
        env.directory_repo,
        env.note_repo,
        env.permission_repo,
    )


@pytest.fixture(scope="function")
async def sharing_service_env(
    spicedb_postgres_env: IntegrationEnv,
) -> IntegrationEnv:
    """Provision a real Postgres + SpiceDB environment for sharing tests."""
    return spicedb_postgres_env


async def assert_user_has_admin_on_directory(
    permission_repo: SpicedbPermissionRepo,
    user_id: str,
    directory_id: str,
    context_factory,
) -> None:
    """Poll SpiceDB until the user is admin on the directory, then assert."""
    resource = ObjectRef(ObjectTypeEnum.DIRECTORY, str(directory_id))
    actor = await context_factory.create(str(user_id))

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
    type: str = "human",
) -> UserEntity:
    """Factory for a human ``UserEntity`` with sensible defaults.

    Sets ``type='human'`` explicitly so the matching DB column
    isn't emitted as ``UNDEFINED`` (which would defer directory
    bootstrap in :func:`UserServiceImpl.create_user`).
    """
    return UserEntity(
        discord_id=discord_id,
        avatar=avatar,
        username=username,
        discriminator=discriminator,
        email=email,
        type=type,
    )


def make_custom_directory(
    *,
    owner_user_id: str,
    slug: str = "project_notes",
    display_name: str = "Project Notes",
    description: str = "Custom parent directory for explicit note placement.",
) -> DirectoryEntity:
    """Factory for a custom directory that grants admin to a user."""
    return DirectoryEntity(
        slug=slug,
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
    # schema helpers
    "load_spicedb_schema",
    "wait_until_spicedb_ready",
    "wait_until",
    # postgres/spicedb env
    "IntegrationEnv",
    "postgres_dsn",
    "spicedb_postgres_env",
    "user_service_env",
    "sharing_service_env",
    # raw spicedb fixtures
    "spicedb_client",
    "spicedb_permission_repo",
    "idempotent_permission_repo",
    # garage
    "garage_config",
    "s3_client",
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
