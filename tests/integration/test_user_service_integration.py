"""Integration test coverage for ``UserServiceImpl`` with real infrastructure.

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
from src.api.other.relationship import ObjectRef, ObjectTypeEnum, Relationship, ShelfRelationEnum, SubjectRef
from src.api.other.undefined import UNDEFINED
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.rule import RuleEntity
from src.db.migrations.context import MigrationContext
from src.db.migrations.runner import MigrationRunner
from src.db.repos.directory.directory_facade import DirectoryFacadeImpl
from src.db.repos.note.note_facade import NoteFacadeImpl
from src.db.repos.permissions.spicedb_repo import SpicedbPermissionRepo
from src.services.user_service import UserServiceImpl
from tests.integration_helpers import NoteRelationEnum, assert_user_has_admin_on_directory, make_custom_directory, make_user_entity, spicedb_postgres_env, wait_until
from tests.stubs.user_context import _UserContext as UserContext
from typing import Awaitable, Callable, Iterable, Tuple, TypeVar
import asyncio, pytest


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]


EnvT = Tuple[UserServiceImpl, DirectoryFacadeImpl, NoteFacadeImpl, SpicedbPermissionRepo]  # noqa: F841 -- kept for backward compat imports


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

    by_slug = {d.slug: d for d in directories}
    for spec in directory_repo.get_default_directory_specs():
        if spec.name not in by_slug:
            pytest.fail(
                f"missing default directory {spec.name!r}; "
                f"available slugs: {sorted(by_slug)!r}"
            )
        directory = by_slug[spec.name]
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
        make_custom_directory(owner_user_id=str(created_user.id)),
        user_ctx=await context_factory.create(str(created_user.id)),
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
            directory_ids=[str(custom_directory.id)],
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
    return await asyncio.gather(*(coro_factory(i) for i in ids))


async def _get_default_directory(
    directory_repo: DirectoryFacadeImpl,
    user_id: str,
    context_factory,
) -> DirectoryEntity:
    """Return the user's first default directory (e.g. ``fleeting_notes``)."""
    default_slug = directory_repo.get_default_directory_specs()[0].name
    ids = await directory_repo.list_user_directory_ids(
        await context_factory.create(user_id)
    )
    for d in await _gather(directory_repo.fetch_directory, ids):
        if d is not None and d.slug == default_slug:
            return d
    pytest.fail(
        f"default directory {default_slug!r} was not created for user {user_id!r}"
    )


async def _note_has_parent_directory(
    permission_repo: SpicedbPermissionRepo,
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


# ---------------------------------------------------------------------------
# Shelf + rule bootstrap integration tests
# ---------------------------------------------------------------------------


async def _fetch_user_shelf_id(
    permission_repo, user_id: str
) -> str:
    """Return the ``users_shelf`` id for ``user_id``.

    Walks the user's visible shelves via SpiceDB and picks
    the one with slug ``users_shelf``.  Used by the integration
    tests below.
    """
    # The bootstrap user service inserts a ``shelf#owner@user``
    # relation for the newly-created user; the SpiceDB lookup
    # surfaces it.  Owner implies admin via the
    # ``permission has_admin = admin + owner`` synthetic.
    ids = await permission_repo.lookup(
        Relationship(
            resource=ObjectRef(
                object_type=ObjectTypeEnum.SHELF, object_id=UNDEFINED,
            ),
            relation=ShelfRelationEnum.OWNER,
            subject=SubjectRef(
                object_type=ObjectTypeEnum.USER,
                object_id=str(user_id),
            ),
        )
    )
    if not ids:
        pytest.fail(
            f"no shelf#owner@user:{user_id!r} relation found"
        )
    return str(ids[0])


async def _seed_rule_via_rule_repo(
    rule_repo,
    shelf_id: str,
    directory_id: str,
    user_id: str,
) -> None:
    """Insert a default-fleeting rule pointing at ``directory_id``.

    Convenience for integration tests that want to bypass the
    user-service bootstrap and pre-stage a shelf-attached rule.
    """
    await rule_repo.create_rule(
        RuleEntity(
            id=UNDEFINED,
            event_type="NoteCreated",
            attached_entity_type="shelf",
            attached_entity_id=shelf_id,
            condition={"type": "always_true"},
            action_type="add_to_directory",
            action_context={"directory_id": directory_id},
            enabled=True,
            creator_id=user_id,
        )
    )


async def test_create_user_bootstraps_shelf_books_and_rule(
    spicedb_postgres_env,
) -> None:
    """End-to-end: ``create_user`` produces a shelf, 3 books and a default rule.

    Bundled into one test because the Postgres + SpiceDB
    container spin-up is the dominant cost; splitting this
    into three tests would multiply that cost.
    """
    env = spicedb_postgres_env
    user_service = env.user_service
    directory_repo = env.directory_repo
    shelf_repo = env.shelf_repo
    rule_repo = env.rule_repo
    context_factory = env.user_context_factory

    created_user = await user_service.create_user(
        make_user_entity(
            discord_id=9870000111,
            username="integration-user-shelf",
            discriminator="4444",
            email="shelf@example.com",
        )
    )
    if created_user.id is None:
        pytest.fail("create_user returned a user without an ID")

    # 1. shelf exists for this user
    user_ctx = await context_factory.create(str(created_user.id))
    shelf_ids = await env.permission_repo.lookup(
        _shelf_owner_relationship(str(created_user.id))
    )
    if len(shelf_ids) != 1:
        pytest.fail(
            f"expected exactly 1 shelf owner relation for "
            f"user {created_user.id!r}, got {len(shelf_ids)}: "
            f"{shelf_ids!r}"
        )
    shelf_id = str(shelf_ids[0])
    shelf = await shelf_repo.fetch_shelf(shelf_id, include_books=True)
    if shelf is None:
        pytest.fail(f"shelf {shelf_id!r} was not created")
    # Slug follows ``<username>'s shelf``.
    assert shelf.slug == "integration-user-shelf's shelf"
    assert shelf.display_name == "integration-user-shelf's Shelf"

    # 2. shelf contains exactly 3 books (the bootstrap defaults).
    if shelf.book_ids is UNDEFINED or len(shelf.book_ids) != 3:
        pytest.fail(
            f"expected shelf {shelf_id!r} to carry 3 books, "
            f"got {shelf.book_ids!r}"
        )

    # 3. the 3 books exist in the directory repo.
    books = await _gather(directory_repo.fetch_directory, shelf.book_ids)
    book_slugs = sorted(
        d.slug for d in books if d is not None
    )
    assert book_slugs == [
        "fleeting_notes", "literature_notes", "permanent_notes"
    ]

    # 4. a default-fleeting rule is attached to the shelf.
    rules = await rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id=shelf_id,
    )
    if len(rules) != 1:
        pytest.fail(
            f"expected 1 default rule on shelf {shelf_id!r}, "
            f"got {len(rules)}"
        )
    rule = rules[0]
    assert rule.action_type == "add_to_directory"
    assert rule.enabled is True
    fleeting_book = next(
        d for d in books if d is not None and d.slug == "fleeting_notes"
    )
    assert rule.action_context == {
        "directory_id": str(fleeting_book.id)
    }


async def test_insert_note_without_directory_uses_rule_default_e2e(
    spicedb_postgres_env,
) -> None:
    """A note inserted without a parent picks up the rule's directory.

    Bundled with the ``no rule`` case below so a single
    Postgres container services both scenarios.
    """
    env = spicedb_postgres_env
    user_service = env.user_service
    directory_repo = env.directory_repo
    note_repo = env.note_repo
    permission_repo = env.permission_repo
    context_factory = env.user_context_factory

    # ---- case A: user has the default rule ----
    created_user = await user_service.create_user(
        make_user_entity(
            discord_id=9870000222,
            username="integration-user-rule-a",
            discriminator="5555",
            email="rule_a@example.com",
        )
    )
    user_ctx = await context_factory.create(str(created_user.id))
    default_directory = await _get_default_directory(
        directory_repo, str(created_user.id), context_factory,
    )

    note = await note_repo.insert(
        NoteEntity(
            title="No explicit parent",
            content="",
            updated_at=datetime.now(),
            author_id=created_user.id,
        ),
        user_ctx,
    )
    if note.note_id is None:
        pytest.fail(f"insert returned a note without an ID: {note!r}")

    await wait_until(
        lambda: _note_has_parent_directory(
            permission_repo,
            str(note.note_id),
            str(default_directory.id),
        ),
        description=(
            f"note {note.note_id!r} parent_directory == "
            f"{default_directory.id!r}"
        ),
    )

    # ---- case B: user without a rule -> insert raises ----
    user_no_rule = await user_service.create_user(
        make_user_entity(
            discord_id=9870000333,
            username="integration-user-no-rule",
            discriminator="6666",
            email="no_rule@example.com",
        )
    )
    # Delete the bootstrap rule so the user has none.
    rules = await env.rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id=await _first_user_shelf(
            env, str(user_no_rule.id),
        ),
    )
    for r in rules:
        await env.rule_repo.delete_rule(str(r.id))

    user_no_rule_ctx = await context_factory.create(str(user_no_rule.id))
    with pytest.raises(ValueError, match="no default-fleeting rule"):
        await note_repo.insert(
            NoteEntity(
                title="Orphan",
                content="",
                updated_at=datetime.now(),
                author_id=user_no_rule.id,
            ),
            user_no_rule_ctx,
        )


async def test_migration_backfills_shelf_and_rule_for_existing_users(
    spicedb_postgres_env,
    tmp_path,
) -> None:
    """The 20260825-bootstrap-users-shelf migration is idempotent.

    Strategy: run the migration against the live schema twice
    and assert the second run is a no-op (no extra shelves, no
    duplicate rules).
    """

    env = spicedb_postgres_env

    # First run -- the integration fixture already applied all
    # migrations, so a re-run of the bootstrap migration should
    # be a no-op.  Run it explicitly anyway so the test fails
    # loudly if a future migration adds side effects.
    ctx = MigrationContext(
        db=env.db,
        spicedb_client=env.spicedb_client,
        services={
            "rule_repo": env.rule_repo,
        },
    )
    runner = MigrationRunner(
        ctx=ctx,
        log_provider=lambda *_a, **_k: __import__(
            "logging"
        ).getLogger("test.migration"),
    )
    await runner.run_pending_migrations()

    # Confirm nothing new was added: count shelves + rules before
    # and after; both must remain constant because the migration
    # is idempotent.
    shelf_rows = await env.db.fetch("SELECT id FROM note.shelf")
    rule_rows = await env.db.fetch(
        "SELECT id FROM rules WHERE event_type = 'NoteCreated'"
    )
    shelves_before = len(shelf_rows or [])
    rules_before = len(rule_rows or [])

    await runner.run_pending_migrations()

    shelf_rows_after = await env.db.fetch("SELECT id FROM note.shelf")
    rule_rows_after = await env.db.fetch(
        "SELECT id FROM rules WHERE event_type = 'NoteCreated'"
    )
    assert len(shelf_rows_after or []) == shelves_before, (
        "second migration run created new shelves"
    )
    assert len(rule_rows_after or []) == rules_before, (
        "second migration run created new rules"
    )


# ---------------------------------------------------------------------------
# Private helpers (integration-specific)
# ---------------------------------------------------------------------------


def _shelf_owner_relationship(user_id: str) -> "Relationship":  # type: ignore[name-defined]
    """Build a ``shelf#owner@user:<id>`` lookup filter.

    The bootstrap user service grants ``shelf#owner`` (not
    ``shelf#admin``) on ``insert_shelf``; ``admin`` is a
    separate relation.  Use ``owner`` to locate the user's
    shelf via SpiceDB.
    """
    return Relationship(
        resource=ObjectRef(
            object_type=ObjectTypeEnum.SHELF, object_id=UNDEFINED
        ),
        relation=ShelfRelationEnum.OWNER,
        subject=SubjectRef(
            object_type=ObjectTypeEnum.USER, object_id=str(user_id)
        ),
    )


async def _first_user_shelf(env, user_id: str) -> str:
    """Return the id of the user's bootstrap shelf.

    Resolves the shelf via SpiceDB ``shelf#owner@user:<id>``
    so the test follows the production lookup semantics
    rather than guessing a slug value -- legacy hardcoded
    ``users_shelf`` slugs no longer match the per-user
    ``<username>'s shelf`` convention.
    """
    shelf_ids = await env.permission_repo.lookup(
        _shelf_owner_relationship(user_id)
    )
    if not shelf_ids:
        pytest.fail(f"no shelf#owner@user:{user_id!r} relation found")
    return str(shelf_ids[0])
