"""SpiceDB-backed ``NotePermissionRepoSpicedb`` fixtures.

The same fixture was duplicated across three integration suites
(`permissions_integration_test`, `test_directory_activity_integration`,
and `test_attachment_integration`).  It lives here now so a single
``consistent`` flag and a single idempotent-insert patch point cover
all three call sites.

The full Postgres + SpiceDB environment is in
:mod:`tests.fixtures.postgres`.  Use that one when your test also
needs the relational store.
"""

from __future__ import annotations

from typing import AsyncIterator

import grpc
import pytest
from testcontainers_spicedb import SpiceDBContainer

from src.db.repos.permissions.permission import NotePermissionRepoSpicedb
from tests._fixtures_pkg.spicedb_schema import (
    SPICEDB_IMAGE,
    create_spicedb_client,
    load_spicedb_schema,
    wait_until_spicedb_ready,
)


@pytest.fixture(scope="function")
async def spicedb_client() -> AsyncIterator:
    """Yield an authzed ``AsyncClient`` connected to a fresh SpiceDB container.

    Useful when a test needs the raw client (for migrations, schema
    inspection, etc.) but not a permission repo.
    """
    with SpiceDBContainer(image=SPICEDB_IMAGE) as spicedb:
        client = create_spicedb_client(
            spicedb.get_endpoint(),
            spicedb.get_secret_key(),
        )
        await wait_until_spicedb_ready(client, load_spicedb_schema())
        yield client


@pytest.fixture(scope="function")
async def spicedb_permission_repo() -> AsyncIterator[NotePermissionRepoSpicedb]:
    """Yield a ``NotePermissionRepoSpicedb`` over a fresh SpiceDB container.

    Same behaviour the three caller suites used to inline:

    * Boots a SpiceDB container with the canonical schema image.
    * Loads the schema once the daemon is reachable.
    * Returns a repo with ``consistent=True`` so writes are visible to
      subsequent reads.
    """
    async for repo in _spicedb_permission_repo_fixture(idempotent_insert=False):
        yield repo


@pytest.fixture(scope="function")
async def idempotent_permission_repo(
) -> AsyncIterator[NotePermissionRepoSpicedb]:
    """Like :func:`spicedb_permission_repo` but with an idempotent ``insert``.

    ``AttachmentFacade`` re-inserts the same ``parent_note`` relationship
    that the test code has already pre-written.  Without the swallow,
    the second insert would raise ``ALREADY_EXISTS``.  This fixture
    wraps :py:meth:`NotePermissionRepoSpicedb.insert` to silence that
    specific error code.
    """
    async for repo in _spicedb_permission_repo_fixture(idempotent_insert=True):
        yield repo


async def _spicedb_permission_repo_fixture(
    *,
    idempotent_insert: bool,
) -> AsyncIterator[NotePermissionRepoSpicedb]:
    """Internal helper used by :func:`spicedb_permission_repo` and friends."""
    with SpiceDBContainer(image=SPICEDB_IMAGE) as spicedb:
        client = create_spicedb_client(
            spicedb.get_endpoint(),
            spicedb.get_secret_key(),
        )
        await wait_until_spicedb_ready(client, load_spicedb_schema())
        repo = NotePermissionRepoSpicedb(client=client, consistent=True)

        if idempotent_insert:
            _wrap_insert_as_idempotent(repo)

        yield repo


def _wrap_insert_as_idempotent(repo: NotePermissionRepoSpicedb) -> None:
    """Wrap ``repo.insert`` so duplicate writes silently return the input.

    Only the ``ALREADY_EXISTS`` grpc error code is swallowed.  Any other
    failure still propagates, so this is safe to use in tests that do
    not expect to re-insert.
    """
    original_insert = repo.insert

    async def _insert_idempotent(relationships):  # type: ignore[no-untyped-def]
        try:
            return await original_insert(relationships)
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.ALREADY_EXISTS:
                return relationships
            raise

    repo.insert = _insert_idempotent  # type: ignore[assignment]


__all__ = [
    "spicedb_client",
    "spicedb_permission_repo",
    "idempotent_permission_repo",
]
