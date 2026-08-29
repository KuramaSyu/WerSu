"""Tests for :class:`SpicedbShelfRepoDecorator`.

The decorator is the seam between the pure storage layer
(:class:`~src.db.repos.shelf.postgres.PostgresShelfRepo`) and
the SpiceDB permission repo.  Every write method has a small,
targeted auth contract:

* ``insert_shelf`` writes ``shelf#owner`` + ``shelf#admin`` for
  the caller.
* ``add_book`` writes ``directory#owner`` + ``directory#admin``
  for the caller.
* ``set_books_of`` writes the same pair, but only for books
  *newly added* in the diff (not for books that were already
  on the shelf).
* ``update_shelf`` / ``delete_shelf`` / ``remove_book`` write
  nothing -- their auth is gated upstream by the service
  layer's permission chain.
* A ``user_ctx`` without a configured ``permission_repo``
  raises :class:`RuntimeError` -- silently dropping the grant
  would be a security bug.

These tests pin each contract to a single behaviour so that
future refactors of the decorator (or the underlying SQL
adapter) cannot regress the auth policy without breaking
something visible.
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from src.api.other.undefined import UNDEFINED, unwrap_undefined
from src.api.other.user_context import UserContextABC
from src.db.repos.shelf.spicedb_decorator import (
    SpicedbShelfRepoDecorator,
    _book_owner_admin,
    _shelf_owner_admin,
)
from src.api.other.relationship import (
    DirectoryRelationEnum,
    ObjectTypeEnum,
    ShelfRelationEnum,
)
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.in_memory_shelf_repo import InMemoryShelfRepo


# ---- helpers --------------------------------------------------------------


class _UserCtx(UserContextABC):
    """Minimal :class:`UserContextABC` for decorator tests."""

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


def _make_decorated(
    *,
    seeded: Optional[dict] = None,
    permission_repo: Optional[InMemoryPermissionRepo] = None,
) -> tuple[SpicedbShelfRepoDecorator, InMemoryShelfRepo, InMemoryPermissionRepo]:
    """Build a decorator + the underlying in-memory repos it wraps."""
    storage = InMemoryShelfRepo(seeded=seeded)
    perm = permission_repo if permission_repo is not None else InMemoryPermissionRepo()
    decorator = SpicedbShelfRepoDecorator(
        inner=storage,
        permission_repo=perm,
    )
    return decorator, storage, perm


def _edges_for(
    *,
    perm: InMemoryPermissionRepo,
    resource_type: str,
    user_id: str,
) -> List:
    """Return the edges the in-memory repo currently holds for ``user_id``.

    Filtered by ``resource_type`` + subject so each test asserts
    against only the rows it cares about -- avoids coupling the
    assertion to whatever unrelated edges the rest of the test
    pipeline writes.
    """
    return [
        rel
        for rel in perm._store  # noqa: SLF001
        if rel.resource.object_type == resource_type
        and rel.subject.object_type == ObjectTypeEnum.USER
        and rel.subject.object_id == user_id
    ]


# ---- insert_shelf --------------------------------------------------------


async def test_insert_shelf_grants_owner_and_admin_on_the_new_shelf() -> None:
    decorator, storage, perm = _make_decorated()

    persisted = await decorator.insert_shelf(slug="my shelf", user_ctx=_UserCtx("u-1"))

    assert unwrap_undefined(persisted.id) == unwrap_undefined(persisted.id)
    # Storage row landed too.
    assert await storage.fetch_shelf(unwrap_undefined(persisted.id)) is not None
    # Two edges: shelf#owner and shelf#admin, both targeting u-1.
    edges = _edges_for(perm=perm, resource_type=ObjectTypeEnum.SHELF, user_id="u-1")
    assert sorted(str(r.relation) for r in edges) == ["admin", "owner"]
    assert all(str(r.resource.object_id) == str(unwrap_undefined(persisted.id)) for r in edges)


async def test_insert_shelf_without_user_ctx_writes_no_edges() -> None:
    decorator, _, perm = _make_decorated()

    await decorator.insert_shelf(slug="my shelf")  # user_ctx omitted

    assert perm._store == []  # noqa: SLF001


async def test_insert_shelf_with_user_ctx_but_no_permission_repo_raises() -> None:
    """A caller identity without an auth adapter is a misconfiguration.

    Silently dropping the grant would be a security bug, so the
    decorator raises :class:`RuntimeError` instead.
    """
    storage = InMemoryShelfRepo()
    decorator = SpicedbShelfRepoDecorator(
        inner=storage,
        permission_repo=None,  # explicitly unwired
    )

    with pytest.raises(RuntimeError, match="permission_repo"):
        await decorator.insert_shelf(slug="my shelf", user_ctx=_UserCtx("u-1"))


# ---- add_book ------------------------------------------------------------


async def test_add_book_grants_owner_and_admin_on_the_book() -> None:
    decorator, _, perm = _make_decorated(seeded={"shelf-1": set()})

    await decorator.add_book("shelf-1", "book-1", user_ctx=_UserCtx("u-1"))

    edges = _edges_for(perm=perm, resource_type=ObjectTypeEnum.DIRECTORY, user_id="u-1")
    assert sorted(str(r.relation) for r in edges) == ["admin", "owner"]
    assert all(str(r.resource.object_id) == "book-1" for r in edges)


async def test_add_book_without_user_ctx_writes_no_edges() -> None:
    decorator, storage, perm = _make_decorated(seeded={"shelf-1": set()})

    await decorator.add_book("shelf-1", "book-1")  # user_ctx omitted

    # Storage happened -- the decorator must not block the
    # underlying write -- but no SpiceDB edges were produced.
    assert await storage.get_books_of("shelf-1") == ["book-1"]
    assert perm._store == []  # noqa: SLF001


async def test_repeated_add_book_writes_edges_each_time_without_error() -> None:
    """SpiceDB's ImportBulkRelationships is idempotent; the decorator
    just forwards every call and trusts the auth layer to dedupe.
    """
    decorator, _, perm = _make_decorated(seeded={"shelf-1": set()})

    await decorator.add_book("shelf-1", "book-1", user_ctx=_UserCtx("u-1"))
    await decorator.add_book("shelf-1", "book-1", user_ctx=_UserCtx("u-1"))

    edges = _edges_for(perm=perm, resource_type=ObjectTypeEnum.DIRECTORY, user_id="u-1")
    # Two insertions, two owner + two admin edges = four rows.
    assert len(edges) == 4
    assert sum(1 for r in edges if str(r.relation) == "owner") == 2
    assert sum(1 for r in edges if str(r.relation) == "admin") == 2


# ---- set_books_of ---------------------------------------------------------


async def test_set_books_of_grants_edges_only_for_newly_added_books() -> None:
    """The diff must scope the grant: already-bound books get no edges."""
    decorator, _, perm = _make_decorated(
        seeded={"shelf-1": {"book-existing"}},
    )

    newly_added = await decorator.set_books_of(
        "shelf-1", ["book-existing", "book-new-1", "book-new-2"],
        user_ctx=_UserCtx("u-1"),
    )

    # The decorator must report the diff upward so the caller
    # (and other decorators in the chain) can introspect.
    assert sorted(newly_added) == ["book-new-1", "book-new-2"]

    edges = _edges_for(perm=perm, resource_type=ObjectTypeEnum.DIRECTORY, user_id="u-1")
    granted_book_ids = {str(r.resource.object_id) for r in edges}
    # Only the new books get owner + admin edges.  ``book-existing``
    # was already on the shelf; the decorator must not re-grant.
    assert granted_book_ids == {"book-new-1", "book-new-2"}
    # And the count is right: two books, each gets owner + admin.
    assert len(edges) == 4


async def test_set_books_of_with_no_op_diff_writes_no_edges() -> None:
    """Re-binding the same set is a no-op for auth too."""
    decorator, _, perm = _make_decorated(
        seeded={"shelf-1": {"book-1", "book-2"}},
    )

    newly_added = await decorator.set_books_of(
        "shelf-1", ["book-1", "book-2"],
        user_ctx=_UserCtx("u-1"),
    )

    assert newly_added == []
    assert perm._store == []  # noqa: SLF001


async def test_set_books_of_returns_diff_to_caller() -> None:
    """``set_books_of`` is the one op whose return value matters.

    The decorator must propagate the inner repo's diff list
    unchanged -- callers that need to scope their own auth
    policy on top of the decorator rely on this.
    """
    decorator, _, _ = _make_decorated(seeded={"shelf-1": set()})

    diff = await decorator.set_books_of(
        "shelf-1", ["book-1", "book-2"],
        user_ctx=_UserCtx("u-1"),
    )

    assert sorted(diff) == ["book-1", "book-2"]


# ---- update_shelf / delete_shelf / remove_book ---------------------------


async def test_update_shelf_writes_no_edges() -> None:
    """Updates don't grant auth -- the service layer gates writes."""
    decorator, _, perm = _make_decorated()

    await decorator.update_shelf(id="shelf-1", slug="renamed", user_ctx=_UserCtx("u-1"))

    assert perm._store == []  # noqa: SLF001


async def test_delete_shelf_writes_no_edges() -> None:
    """Deletes don't grant auth either."""
    decorator, _, perm = _make_decorated()

    await decorator.delete_shelf("shelf-1", user_ctx=_UserCtx("u-1"))

    assert perm._store == []  # noqa: SLF001


async def test_remove_book_writes_no_edges() -> None:
    """Removing a binding must not revoke the user's owner / admin
    edges on the underlying book.  The decorator reflects this by
    writing nothing -- the contract is "don't churn auth on
    unbind"."""
    decorator, _, perm = _make_decorated(
        seeded={"shelf-1": {"book-1"}},
    )

    await decorator.remove_book("shelf-1", "book-1", user_ctx=_UserCtx("u-1"))

    assert perm._store == []  # noqa: SLF001


# ---- read methods pass through unchanged ---------------------------------


async def test_fetch_shelf_passes_through_to_inner() -> None:
    decorator, storage, _ = _make_decorated()
    inserted = await storage.insert_shelf(slug="my shelf", display_name="My Shelf")

    entity = await decorator.fetch_shelf(unwrap_undefined(inserted.id))

    assert entity is not None
    assert entity.slug == "my shelf"


async def test_get_books_of_returns_inner_list() -> None:
    decorator, storage, _ = _make_decorated(seeded={"shelf-1": {"book-a", "book-b"}})

    assert await decorator.get_books_of("shelf-1") == ["book-a", "book-b"]


# ---- edge builders --------------------------------------------------------


def test_shelf_owner_admin_helpers_emit_owner_and_admin_in_order() -> None:
    """Lock down the helper output so the auth policy is auditable.

    ``_shelf_owner_admin`` is the single source of truth for
    the shelf#owner / shelf#admin edge pair; the rest of the
    decorator derives from it.  A regression here flips the
    whole policy, so the order matters.
    """
    edges = _shelf_owner_admin("shelf-1", "user-1")

    assert [str(r.relation) for r in edges] == ["owner", "admin"]
    assert all(
        r.resource.object_type == ObjectTypeEnum.SHELF
        and str(r.resource.object_id) == "shelf-1"
        and r.subject.object_type == ObjectTypeEnum.USER
        and str(r.subject.object_id) == "user-1"
        for r in edges
    )
    assert all(str(r.relation) in ShelfRelationEnum.__members__.values() for r in edges)


def test_book_owner_admin_helpers_emit_owner_and_admin_on_directory() -> None:
    edges = _book_owner_admin("book-1", "user-1")

    assert [str(r.relation) for r in edges] == ["owner", "admin"]
    assert all(
        r.resource.object_type == ObjectTypeEnum.DIRECTORY
        and str(r.resource.object_id) == "book-1"
        and r.subject.object_type == ObjectTypeEnum.USER
        and str(r.subject.object_id) == "user-1"
        for r in edges
    )
    assert all(str(r.relation) in DirectoryRelationEnum.__members__.values() for r in edges)
