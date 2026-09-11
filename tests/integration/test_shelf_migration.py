"""Integration tests for the ``20260825-bootstrap-users-shelf`` migration.

The migration is re-runnable: deleting its row from
``public.schema_migrations`` and calling
``MigrationRunner.run_pending_migrations`` again exercises the
same code path with every service wired (SpiceDB, shelf repo,
directory facade, zettelkasten strategy).  This module
deliberately bypasses ``user_service.create_user`` (which would
run the bootstrap on its own) by inserting the user directly via
``user_repo.insert`` so we can assert that pre-existing books and
the resulting shelf / rule wiring match the contract documented on
the migration module.
"""

from __future__ import annotations

from src.api.other.relationship import (
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    ShelfRelationEnum,
    SubjectRef,
)
from src.api.other.undefined import UNDEFINED
from src.db.entities.directory.directory import DirectoryEntity
from src.db.migrations.context import MigrationContext, MigrationServices
from src.db.migrations.runner import MigrationRunner
from src.services.shelf_bootstrap import build_strategy
from src.utils import logging_provider
from tests.integration_helpers import (
    IntegrationEnv,
    make_user_entity,
    spicedb_postgres_env,
)
from typing import Awaitable, Callable, Dict, Iterable, List, Tuple, TypeVar
import asyncio, pytest, random


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shelf_owner_relationship(user_id: str) -> Relationship:
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


_T = TypeVar("_T")


async def _gather(
    coro_factory: Callable[[str], Awaitable[_T]], ids: Iterable[str]
) -> list[_T]:
    """Await a coroutine for each id in parallel."""
    return await asyncio.gather(*(coro_factory(i) for i in ids))


async def _seed_user_with_books(
    env: IntegrationEnv,
    *,
    username: str,
    discriminator: str,
    book_specs: List[Tuple[str, str, str]],
) -> Tuple[str, Dict[str, str]]:
    """Create a user directly and seed books via the facade.

    Bypasses ``user_service.create_user`` so no bootstrap runs
    here -- that is what the migration is for.  Returns
    ``(user_id, slug -> book_id)`` so each test can assert
    that pre-existing books survive the migration intact.
    """
    user_entity = make_user_entity(
        discord_id=random.randint(1_000_000_000, 9_999_999_999),
        username=username,
        discriminator=discriminator,
        email=f"{username}@example.com",
    )
    created_user = await env.user_repo.insert(user_entity)
    if created_user.id is None:
        pytest.fail("user_repo.insert returned no id")
    user_id = str(created_user.id)
    user_ctx = await env.user_context_factory.create(user_id)

    slug_to_book_id: Dict[str, str] = {}
    for slug, display_name, description in book_specs:
        created_book = await env.directory_repo.create_directory(
            DirectoryEntity(
                slug=slug,
                display_name=display_name,
                description=description,
            ),
            user_ctx,
        )
        if created_book.id is None:
            pytest.fail(f"create_directory returned no id for {slug!r}")
        slug_to_book_id[slug] = str(created_book.id)

    return user_id, slug_to_book_id


async def _rerun_bootstrap_migration(env: IntegrationEnv) -> None:
    """Re-run only the bootstrap-users-shelf migration with full services wired.

    The env fixture applies every migration once.  Deleting the
    bootstrap row from ``public.schema_migrations`` makes the
    runner pick it up as pending; the wiring of every service
    in :class:`MigrationServices` means the migration's helper
    code paths actually execute instead of short-circuiting on
    ``None``.
    """
    await env.db.execute(
        "DELETE FROM public.schema_migrations WHERE migration_name = $1",
        "20260825-bootstrap-users-shelf",
    )
    zettelkasten_strategy = build_strategy(
        "zettelkasten",
        shelf_repo=env.shelf_repo,
        rule_repo=env.rule_repo,
        directory_facade=env.directory_repo,
    )
    ctx = MigrationContext(
        db=env.db,
        spicedb_client=env.spicedb_client,
        services=MigrationServices(
            permission_repo=env.permission_repo,
            rule_repo=env.rule_repo,
            shelf_repo=env.shelf_repo,
            directory_facade=env.directory_repo,
            user_context_factory=env.user_context_factory,
            zettelkasten_strategy=zettelkasten_strategy,
        ),
    )
    runner = MigrationRunner(
        ctx=ctx,
        log_provider=logging_provider,
    )
    await runner.run_pending_migrations()


# ---------------------------------------------------------------------------
# Migration behaviour tests
# ---------------------------------------------------------------------------


_MIGRATION_BOOK_CASES: List[Tuple[str, List[Tuple[str, str, str]], int, List[str]]] = [
    # (label, pre_existing_books, expected_total_book_count, expected_slugs_on_shelf)
    (
        "no_pre_existing_books",
        [],
        3,
        ["fleeting_notes", "literature_notes", "permanent_notes"],
    ),
    (
        "custom_books_only",
        [
            ("my_project", "My Project", "Project notes"),
            ("work_journal", "Work Journal", "Work journal"),
        ],
        5,
        [
            "fleeting_notes", "literature_notes", "permanent_notes",
            "my_project", "work_journal",
        ],
    ),
    (
        "pre_existing_fleeting",
        [("fleeting_notes", "Pre-existing Fleeting", "Pre-existing fleeting")],
        3,
        ["fleeting_notes", "literature_notes", "permanent_notes"],
    ),
    (
        "all_pre_existing_defaults",
        [
            ("fleeting_notes", "Pre-existing Fleeting", "Pre-existing fleeting"),
            ("literature_notes", "Pre-existing Literature", "Pre-existing literature"),
            ("permanent_notes", "Pre-existing Permanent", "Pre-existing permanent"),
        ],
        3,
        ["fleeting_notes", "literature_notes", "permanent_notes"],
    ),
]


@pytest.mark.parametrize(
    "label,pre_books,expected_book_count,expected_slugs",
    _MIGRATION_BOOK_CASES,
    ids=[case[0] for case in _MIGRATION_BOOK_CASES],
)
async def test_migration_bootstrap_for_various_pre_states(
    spicedb_postgres_env,
    label: str,
    pre_books: List[Tuple[str, str, str]],
    expected_book_count: int,
    expected_slugs: List[str],
) -> None:
    """A single end-to-end check covering the four pre-state shapes.

    Each parameter case seeds a user with a different mix of
    pre-existing books, runs the bootstrap migration, and
    asserts:

    * exactly one shelf with the expected number of books;
    * the shelf carries the union of pre-existing + bootstrap
      default books (verified by slug set);
    * the NoteCreated rule points at the (single) fleeting book
      on the shelf -- reusing a pre-existing one when present,
      creating a fresh one otherwise;
    * the user can ``delete`` the shelf and every book on it
      (admin derives from owner on the shelf via the
      derive-shelf-admin-from-owner migration; books carry
      admin directly via ``create_directory``'s grant).
    """
    env = spicedb_postgres_env

    username = f"bootstrap-{label.replace('_', '-')}"
    user_id, slug_to_book_id = await _seed_user_with_books(
        env,
        username=username,
        discriminator=str(random.randint(1000, 9999)),
        book_specs=pre_books,
    )

    await _rerun_bootstrap_migration(env)

    # 1. shelf exists exactly once for the user
    shelf_ids = await env.permission_repo.lookup(
        _shelf_owner_relationship(user_id)
    )
    if len(shelf_ids) != 1:
        pytest.fail(f"expected 1 shelf, got {len(shelf_ids)}: {shelf_ids!r}")
    shelf_id = str(shelf_ids[0])

    shelf = await env.shelf_repo.fetch_shelf(shelf_id, include_books=True)
    if shelf is None:
        pytest.fail(f"shelf {shelf_id!r} not found")
    if shelf.book_ids is UNDEFINED or len(shelf.book_ids) != expected_book_count:
        pytest.fail(
            f"expected {expected_book_count} books on shelf {shelf_id!r}, "
            f"got {shelf.book_ids!r}"
        )

    # 2. shelf books match the expected slug set
    books = await _gather(env.directory_repo.fetch_directory, shelf.book_ids)
    book_slugs = sorted(d.slug for d in books if d is not None)
    assert book_slugs == sorted(expected_slugs), (
        f"unexpected book slugs: got {book_slugs!r}, "
        f"expected {sorted(expected_slugs)!r}"
    )

    # 3. pre-existing books survive (idempotent on re-runs)
    for slug, book_id in slug_to_book_id.items():
        assert book_id in shelf.book_ids, (
            f"pre-existing {slug!r} ({book_id!r}) missing from shelf "
            f"{sorted(shelf.book_ids)!r}"
        )

    # 4. exactly one NoteCreated rule, pointing at the shelf's fleeting book
    rules = await env.rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id=shelf_id,
    )
    if len(rules) != 1:
        pytest.fail(f"expected 1 NoteCreated rule, got {len(rules)}")
    rule = rules[0]
    assert rule.action_type == "add_to_directory"
    fleeting = next(
        d for d in books if d is not None and d.slug == "fleeting_notes"
    )
    assert rule.action_context == {"directory_id": str(fleeting.id)}, (
        f"rule points at wrong directory: {rule.action_context!r}"
    )

    # 5. user can delete the shelf and every book (admin derivation)
    user_ctx = await env.user_context_factory.create(user_id)
    shelf_resource = ObjectRef(ObjectTypeEnum.SHELF, shelf_id)
    if not await env.permission_repo.has_permission(
        user_ctx, "delete", shelf_resource
    ):
        pytest.fail(
            "user should be able to delete the shelf after migration"
        )
    for book_id in shelf.book_ids:
        book_resource = ObjectRef(ObjectTypeEnum.DIRECTORY, str(book_id))
        if not await env.permission_repo.has_permission(
            user_ctx, "delete", book_resource
        ):
            pytest.fail(
                f"user should be able to delete book {book_id!r} on shelf "
                f"{shelf_id!r} after migration"
            )


__all__ = [
    "test_migration_bootstrap_for_various_pre_states",
]