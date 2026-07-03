"""Integration test coverage for ``UserService`` with real infrastructure.

These tests exercise the full user-bootstrap path against real Postgres
and SpiceDB containers to validate that:

1. the user is persisted in Postgres,
2. default zettelkasten directories are created in Postgres,
3. directory permission relationships are written/read via SpiceDB,
4. notes inherit the default directory when no parent is specified,
5. notes respect an explicit parent directory when one is provided.

The tests are marked ``integration`` and ``spicedb`` and live under
``tests/integration/``; they are excluded from the default test
run configured in ``pytest.ini``.
"""

from datetime import datetime
from typing import Awaitable, Callable, Iterable, Tuple, TypeVar

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.directory.directory import DirectoryRepoSpicedbPostgres
from src.db.repos.note.note import NoteFacade
from src.db.repos.permissions.permission import NotePermissionRepoSpicedb
from src.services.user import UserService
from tests.integration_helpers import (
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    assert_user_has_admin_on_directory,
    make_custom_directory,
    make_user_entity,
    spicedb_postgres_env,
    wait_until,
)


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]


EnvT = Tuple[UserService, DirectoryRepoSpicedbPostgres, NoteFacade, NotePermissionRepoSpicedb]  # noqa: F841 -- kept for backward compat imports


async def test_create_user_bootstraps_default_directories(
    spicedb_postgres_env,
) -> None:
    """New users get the three default zettelkasten directories and admin rights.

    Each default directory must match its configured spec and the
    creating user must end up with admin, write, and view permissions
    on it (and a different user must not).
    """
    user_service, directory_repo, permission_repo, context_factory = spicedb_postgres_env.user_service, spicedb_postgres_env.directory_repo, spicedb_postgres_env.permission_repo, spicedb_postgres_env.user_context_factory

    created_user = await user_service.create_user(
        make_user_entity(
            discord_id=1357913579,
            username="integration-user",
            discriminator="4321",
            email="integration@example.com",
        )
    )
    if created_user.id is None:
        pytest.fail(f"create_user() returned a user without an ID: {created_user!r}")

    # Round-trip read through the same service that wrote the user.
    fetched_user = await user_service.get_user(user_id=created_user.id)
    if fetched_user is None:
        pytest.fail(
            f"get_user(user_id={created_user.id!r}) returned None for the "
            f"just-created user"
        )
    assert fetched_user.id == created_user.id, (
        f"round-tripped user has a different ID: created={created_user.id!r} "
        f"vs fetched={fetched_user.id!r}"
    )

    # All three default directories are created.
    directory_ids = await directory_repo.list_user_directory_ids(
        await context_factory.create(str(created_user.id))
    )
    if len(directory_ids) != 3:
        pytest.fail(
            f"expected 3 default directories for user {created_user.id!r}, "
            f"got {len(directory_ids)}: {directory_ids!r}"
        )

    directories = [
        d
        for d in await _gather(directory_repo.fetch_directory, directory_ids)
        if d is not None
    ]
    if len(directories) != 3:
        pytest.fail(
            f"fetch_directory returned None for {len(directory_ids) - len(directories)} "
            f"of the {len(directory_ids)} directory IDs: {directory_ids!r}"
        )

    by_name = {d.name: d for d in directories}
    for spec in directory_repo.get_default_directory_specs():
        if spec.name not in by_name:
            pytest.fail(
                f"missing default directory {spec.name!r}; "
                f"available names: {sorted(by_name)!r}"
            )
        directory = by_name[spec.name]
        if directory.id is None:
            pytest.fail(
                f"fetched directory {spec.name!r} has no ID: {directory!r}"
            )
        assert directory.display_name == spec.display_name, (
            f"display_name mismatch for {spec.name!r}: "
            f"expected {spec.display_name!r}, got {directory.display_name!r}"
        )
        assert directory.description == spec.description, (
            f"description mismatch for {spec.name!r}: "
            f"expected {spec.description!r}, got {directory.description!r}"
        )

        # Permissions become visible eventually; wait, then assert all of them.
        await assert_user_has_admin_on_directory(
            permission_repo, str(created_user.id), str(directory.id), context_factory
        )
        if await permission_repo.has_permission(
            await context_factory.create("another-user"),
            "view",
            ObjectRef(ObjectTypeEnum.DIRECTORY, str(directory.id)),
        ):
            pytest.fail(
                f"unexpected 'view' permission for 'another-user' on directory "
                f"{directory.id!r} ({spec.name!r})"
            )


async def test_insert_note_uses_default_fleeting_directory_when_parent_not_specified(
    spicedb_postgres_env,
) -> None:
    """Notes without a parent attach to the default ``fleeting_notes`` directory."""
    user_service, directory_repo, note_repo, permission_repo, context_factory = spicedb_postgres_env.user_service, spicedb_postgres_env.directory_repo, spicedb_postgres_env.note_repo, spicedb_postgres_env.permission_repo, spicedb_postgres_env.user_context_factory

    created_user = await user_service.create_user(
        make_user_entity(
            discord_id=2468024680,
            username="integration-user-2",
            discriminator="2222",
            email="integration2@example.com",
            avatar="https://cdn.example/avatar-2.png",
        )
    )
    if created_user.id is None:
        pytest.fail(f"create_user() returned a user without an ID: {created_user!r}")

    default_directory = await _get_default_directory(directory_repo, created_user.id, context_factory)
    if default_directory.id is None:
        pytest.fail(
            f"default directory {default_directory.name!r} was created without an ID: "
            f"{default_directory!r}"
        )

    note = await note_repo.insert(
        NoteEntity(
            title="No explicit parent",
            content="",
            updated_at=datetime.now(),
            author_id=created_user.id,
        ),
        await context_factory.create(str(created_user.id)),
    )
    if note.note_id is None:
        pytest.fail(f"insert() returned a note without an ID: {note!r}")

    await wait_until(
        lambda: _note_has_parent_directory(
            permission_repo,
            str(note.note_id),
            str(default_directory.id),
        ),
        description=f"note {note.note_id!r} parent_directory == {default_directory.id!r}",
    )


async def test_insert_note_uses_specified_parent_directory_when_provided(
    spicedb_postgres_env,
) -> None:
    """Notes with an explicit parent attach to that parent directory."""
    user_service, directory_repo, note_repo, permission_repo, context_factory = spicedb_postgres_env.user_service, spicedb_postgres_env.directory_repo, spicedb_postgres_env.note_repo, spicedb_postgres_env.permission_repo, spicedb_postgres_env.user_context_factory

    created_user = await user_service.create_user(
        make_user_entity(
            discord_id=1122334455,
            username="integration-user-3",
            discriminator="3333",
            email="integration3@example.com",
            avatar="https://cdn.example/avatar-3.png",
        )
    )
    if created_user.id is None:
        pytest.fail(f"create_user() returned a user without an ID: {created_user!r}")

    custom_directory = await directory_repo.create_directory(
        make_custom_directory(owner_user_id=str(created_user.id))
    )
    if custom_directory.id is None:
        pytest.fail(
            f"create_directory() returned a directory without an ID: {custom_directory!r}"
        )

    note = await note_repo.insert(
        NoteEntity(
            title="Explicit parent",
            content="",
            updated_at=datetime.now(),
            author_id=created_user.id,
            parent_dir_id=str(custom_directory.id),
        ),
        await context_factory.create(str(created_user.id)),
    )
    if note.note_id is None:
        pytest.fail(f"insert() returned a note without an ID: {note!r}")

    await wait_until(
        lambda: _note_has_parent_directory(
            permission_repo,
            str(note.note_id),
            str(custom_directory.id),
        ),
        description=f"note {note.note_id!r} parent_directory == {custom_directory.id!r}",
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


async def _gather(
    coro_factory: Callable[[str], Awaitable[_T]], ids: Iterable[str]
) -> list[_T]:
    """Await a coroutine for each id in parallel."""
    import asyncio
    return await asyncio.gather(*(coro_factory(i) for i in ids))


async def _get_default_directory(
    directory_repo: DirectoryRepoSpicedbPostgres,
    user_id: str,
    context_factory,
) -> DirectoryEntity:
    """Return the user's first default directory (e.g. ``fleeting_notes``)."""
    default_name = directory_repo.get_default_directory_specs()[0].name
    ids = await directory_repo.list_user_directory_ids(
        await context_factory.create(user_id)
    )
    for d in await _gather(directory_repo.fetch_directory, ids):
        if d is not None and d.name == default_name:
            return d
    pytest.fail(
        f"default directory {default_name!r} was not created for user {user_id!r}"
    )


async def _note_has_parent_directory(
    permission_repo: NotePermissionRepoSpicedb,
    note_id: str,
    parent_directory_id: str,
) -> bool:
    """True when the note has a ``parent_directory`` relation to the given id."""
    relationships = await permission_repo.list_relationships(
        ObjectRef(ObjectTypeEnum.NOTE, note_id)
    )
    return any(
        str(rel.relation) == NoteRelationEnum.PARENT_DIRECTORY.value
        and str(rel.subject.object_id) == parent_directory_id
        for rel in relationships
    )
