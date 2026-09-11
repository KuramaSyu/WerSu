"""Unit tests for the ``20260825-bootstrap-users-shelf`` migration.

Drives the migration's ``_bind_user_books`` helper against the
in-memory test doubles (``InMemoryShelfRepo`` +
``InMemoryPermissionRepo``) so we can exercise the
bind-unassigned-books logic without spinning up a Postgres +
SpiceDB container.  The contract under test:

* Books owned by the user (``directory#admin@user:<id>``) and
  not yet on any shelf get bound to the freshly-inserted shelf
  via ``shelf_repo.add_book``.
* Books already on some other shelf are left untouched.
* Books the user does not own are not touched.

Integration tests for the end-to-end migration (full Postgres +
SpiceDB wiring) live in :tests/integration/test_shelf_migration.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Optional

from src.api.other.relationship import (
    DirectoryRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import UserContextABC
from src.db.entities.shelf import ShelfEntity
from src.db.migrations.context import MigrationContext, MigrationServices
from src.services.user_service import users_shelf_slug_for
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.in_memory_shelf_repo import InMemoryShelfRepo
import pytest


# ---- helpers --------------------------------------------------------------


class _UserCtx(UserContextABC):
    def __init__(self, user_id: str) -> None:
        self._uid = user_id

    @property
    def user_id(self) -> str:
        return self._uid

    @property
    def type(self):  # type: ignore[override]
        return UNDEFINED

    async def is_temporary_user(self) -> bool:
        return False


def _make_migration():
    """Build a :class:`Migration` instance wired for unit testing.

    The constructor needs a ``migration_path`` and a
    ``log_provider`` (from :class:`MigrationABC`).  We pass
    placeholders; the helpers under test don't touch either.
    """
    path = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "db"
        / "migrations"
        / "20260825-bootstrap-users-shelf.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_bootstrap_users_shelf", str(path)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load migration module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    migration = module.Migration(
        migration_path=path,
        log_provider=lambda *_a, **_k: __import__("logging").getLogger(
            "test.migration"
        ),
    )
    return migration


def _make_ctx(
    *,
    shelf_repo: Optional[InMemoryShelfRepo] = None,
    permission_repo: Optional[InMemoryPermissionRepo] = None,
) -> MigrationContext:
    """Build a :class:`MigrationContext` with only the fields the helper reads."""
    return MigrationContext(
        db=None,
        services=MigrationServices(
            shelf_repo=shelf_repo,
            permission_repo=permission_repo,
        ),
    )


async def _grant_admin(
    permission_repo: InMemoryPermissionRepo,
    book_id: str,
    user_id: str,
) -> None:
    """Insert a ``directory#admin@user`` edge for ``book_id``."""
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, book_id),
                relation=DirectoryRelationEnum.ADMIN,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        ]
    )


# ---- tests ----------------------------------------------------------------


async def test_bind_user_books_binds_all_owned_unassigned_books() -> None:
    """Every admin-book the user owns lands on the new shelf."""
    migration = _make_migration()
    user_id = "u-1"
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    for bid in ("b-1", "b-2", "b-3"):
        await _grant_admin(permission_repo, bid, user_id)

    shelf = await shelf_repo.insert_shelf(
        slug=users_shelf_slug_for("alice"),
        display_name="Alice's Shelf",
        description="",
        user_ctx=_UserCtx(user_id),
    )

    await _make_migration()._bind_user_books(
        _make_ctx(shelf_repo=shelf_repo, permission_repo=permission_repo),
        shelf=shelf,
        user_id=user_id,
        user_ctx=_UserCtx(user_id),
    )

    assert sorted(await shelf_repo.get_books_of(str(shelf.id))) == [
        "b-1", "b-2", "b-3",
    ]


async def test_bind_user_books_skips_books_already_on_a_shelf() -> None:
    """Books already on a shelf must not be moved to the new one."""
    migration = _make_migration()
    user_id = "u-1"
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo(seeded={"other-shelf": {"b-existing"}})

    for bid in ("b-existing", "b-new"):
        await _grant_admin(permission_repo, bid, user_id)

    new_shelf = await shelf_repo.insert_shelf(
        slug=users_shelf_slug_for("bob"),
        display_name="Bob's Shelf",
        description="",
        user_ctx=_UserCtx(user_id),
    )

    await _make_migration()._bind_user_books(
        _make_ctx(shelf_repo=shelf_repo, permission_repo=permission_repo),
        shelf=new_shelf,
        user_id=user_id,
        user_ctx=_UserCtx(user_id),
    )

    # existing binding untouched, new book added to new shelf only
    assert await shelf_repo.get_books_of("other-shelf") == ["b-existing"]
    assert await shelf_repo.get_books_of(str(new_shelf.id)) == ["b-new"]
    assert sorted(await shelf_repo.get_shelves_of_book("b-existing")) == [
        "other-shelf",
    ]
    assert sorted(await shelf_repo.get_shelves_of_book("b-new")) == [
        str(new_shelf.id),
    ]


async def test_bind_user_books_is_noop_when_no_owned_books() -> None:
    """A user with no admin edges leaves the new shelf empty."""
    migration = _make_migration()
    user_id = "u-1"
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    shelf = await shelf_repo.insert_shelf(
        slug=users_shelf_slug_for("carol"),
        display_name="Carol's Shelf",
        description="",
        user_ctx=_UserCtx(user_id),
    )

    await _make_migration()._bind_user_books(
        _make_ctx(shelf_repo=shelf_repo, permission_repo=permission_repo),
        shelf=shelf,
        user_id=user_id,
        user_ctx=_UserCtx(user_id),
    )

    assert await shelf_repo.get_books_of(str(shelf.id)) == []


async def test_bind_user_books_ignores_books_user_does_not_own() -> None:
    """Books owned by another user are not affected by the bind."""
    migration = _make_migration()
    me = "u-me"
    other = "u-other"
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    await _grant_admin(permission_repo, "b-mine", me)
    await _grant_admin(permission_repo, "b-theirs", other)

    shelf = await shelf_repo.insert_shelf(
        slug=users_shelf_slug_for("dave"),
        display_name="Dave's Shelf",
        description="",
        user_ctx=_UserCtx(me),
    )

    await _make_migration()._bind_user_books(
        _make_ctx(shelf_repo=shelf_repo, permission_repo=permission_repo),
        shelf=shelf,
        user_id=me,
        user_ctx=_UserCtx(me),
    )

    assert await shelf_repo.get_books_of(str(shelf.id)) == ["b-mine"]


async def test_bind_user_books_noop_when_services_unwired() -> None:
    """A Postgres-only / fixture-only run hits the short-circuit.

    Mirrors the migration's own ``None`` guard so the fixture
    path doesn't crash when no spicedb layer is wired.
    """
    migration = _make_migration()
    shelf = ShelfEntity(
        id="shelf-1",
        slug="x",
    )

    # both services None
    await _make_migration()._bind_user_books(
        MigrationContext(db=None),
        shelf=shelf,
        user_id="u-1",
        user_ctx=_UserCtx("u-1"),
    )

    # only shelf_repo wired (permission_repo None)
    await _make_migration()._bind_user_books(
        MigrationContext(
            db=None,
            services=MigrationServices(shelf_repo=InMemoryShelfRepo()),
        ),
        shelf=shelf,
        user_id="u-1",
        user_ctx=_UserCtx("u-1"),
    )

    # only permission_repo wired (shelf_repo None)
    await _make_migration()._bind_user_books(
        MigrationContext(
            db=None,
            services=MigrationServices(permission_repo=InMemoryPermissionRepo()),
        ),
        shelf=shelf,
        user_id="u-1",
        user_ctx=_UserCtx("u-1"),
    )


__all__ = [
    "test_bind_user_books_binds_all_owned_unassigned_books",
    "test_bind_user_books_skips_books_already_on_a_shelf",
    "test_bind_user_books_is_noop_when_no_owned_books",
    "test_bind_user_books_ignores_books_user_does_not_own",
    "test_bind_user_books_noop_when_services_unwired",
]