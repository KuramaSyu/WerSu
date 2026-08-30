"""Tests for :class:`SpicedbShelfRepoDecorator`.

Contract:

* ``insert_shelf`` writes ``shelf#owner`` for the caller.
* ``delete_shelf`` revokes every ``shelf#*@*`` edge on the id.
* ``add_book`` / ``set_books_of`` (added half) insert
  ``directory:<book>#parent@shelf:<shelf>``.
* ``remove_book`` / ``set_books_of`` (removed half) delete that edge.
* A write op that needs ``permission_repo`` raises ``RuntimeError``
  when none is wired in.
"""

from __future__ import annotations

from typing import Optional

import pytest

from src.api.other.undefined import UNDEFINED, unwrap_undefined
from src.api.other.user_context import UserContextABC
from src.api.other.relationship import (
    DirectoryRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    ShelfRelationEnum,
    SubjectRef,
)
from src.db.repos.shelf.spicedb_decorator import SpicedbShelfRepoDecorator
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.in_memory_shelf_repo import InMemoryShelfRepo


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


def _make_decorated(
    *,
    seeded: Optional[dict] = None,
    permission_repo: Optional[InMemoryPermissionRepo] = None,
):
    storage = InMemoryShelfRepo(seeded=seeded)
    perm = permission_repo if permission_repo is not None else InMemoryPermissionRepo()
    decorator = SpicedbShelfRepoDecorator(inner=storage, permission_repo=perm)
    return decorator, storage, perm


def _shelf_edges(perm: InMemoryPermissionRepo, *, user_id: str):
    return [
        r for r in perm._store  # noqa: SLF001
        if r.resource.object_type == ObjectTypeEnum.SHELF
        and r.subject.object_type == ObjectTypeEnum.USER
        and str(r.subject.object_id) == user_id
    ]


def _directory_parent_edges(perm: InMemoryPermissionRepo, *, shelf_id: str):
    return [
        r for r in perm._store  # noqa: SLF001
        if r.resource.object_type == ObjectTypeEnum.DIRECTORY
        and str(r.relation) == DirectoryRelationEnum.PARENT
        and r.subject.object_type == ObjectTypeEnum.SHELF
        and str(r.subject.object_id) == shelf_id
    ]


# ---- insert_shelf --------------------------------------------------------


async def test_insert_shelf_grants_owner_on_the_new_shelf() -> None:
    decorator, storage, perm = _make_decorated()
    persisted = await decorator.insert_shelf(slug="my shelf", user_ctx=_UserCtx("u-1"))

    assert await storage.fetch_shelf(unwrap_undefined(persisted.id)) is not None
    edges = _shelf_edges(perm, user_id="u-1")
    assert [str(r.relation) for r in edges] == ["owner"]
    assert all(str(r.resource.object_id) == str(unwrap_undefined(persisted.id)) for r in edges)


async def test_insert_shelf_without_user_ctx_writes_no_edges() -> None:
    decorator, _, perm = _make_decorated()
    await decorator.insert_shelf(slug="my shelf")
    assert perm._store == []  # noqa: SLF001


async def test_insert_shelf_without_permission_repo_is_a_type_error() -> None:
    """``permission_repo`` is required: omitting it fails at construction."""
    with pytest.raises(TypeError):
        SpicedbShelfRepoDecorator(  # type: ignore[call-arg]
            inner=InMemoryShelfRepo(),
        )


# ---- add_book ------------------------------------------------------------


async def test_add_book_inserts_directory_parent_edge_to_shelf() -> None:
    decorator, _, perm = _make_decorated(seeded={"shelf-1": set()})
    await decorator.add_book("shelf-1", "book-1")
    edges = _directory_parent_edges(perm, shelf_id="shelf-1")
    assert len(edges) == 1
    assert str(edges[0].resource.object_id) == "book-1"


async def test_add_book_without_permission_repo_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        SpicedbShelfRepoDecorator(  # type: ignore[call-arg]
            inner=InMemoryShelfRepo(seeded={"shelf-1": set()}),
        )


# ---- remove_book ---------------------------------------------------------


async def test_remove_book_deletes_directory_parent_edge_to_shelf() -> None:
    decorator, _, perm = _make_decorated(seeded={"shelf-1": {"book-1"}})
    await decorator.add_book("shelf-1", "book-1")
    await decorator.remove_book("shelf-1", "book-1")
    assert _directory_parent_edges(perm, shelf_id="shelf-1") == []


# ---- set_books_of --------------------------------------------------------


async def test_set_books_of_inserts_parent_edge_for_newly_added_books() -> None:
    decorator, _, perm = _make_decorated(seeded={"shelf-1": {"book-existing"}})

    newly_added = await decorator.set_books_of(
        "shelf-1", ["book-existing", "book-new-1", "book-new-2"],
    )
    assert sorted(newly_added) == ["book-new-1", "book-new-2"]

    granted = {
        str(r.resource.object_id)
        for r in _directory_parent_edges(perm, shelf_id="shelf-1")
    }
    assert granted == {"book-new-1", "book-new-2"}


async def test_set_books_of_deletes_parent_edge_for_removed_books() -> None:
    decorator, _, perm = _make_decorated(seeded={"shelf-1": {"book-stay", "book-leave"}})
    await decorator.add_book("shelf-1", "book-stay")
    await decorator.add_book("shelf-1", "book-leave")

    await decorator.set_books_of("shelf-1", ["book-stay"])

    granted = {
        str(r.resource.object_id)
        for r in _directory_parent_edges(perm, shelf_id="shelf-1")
    }
    assert granted == {"book-stay"}


async def test_set_books_of_with_no_op_diff_writes_no_edges() -> None:
    decorator, _, perm = _make_decorated(seeded={"shelf-1": {"book-1", "book-2"}})
    newly_added = await decorator.set_books_of("shelf-1", ["book-1", "book-2"])
    assert newly_added == []
    assert perm._store == []  # noqa: SLF001


async def test_set_books_of_returns_diff_to_caller() -> None:
    decorator, _, _ = _make_decorated(seeded={"shelf-1": set()})
    diff = await decorator.set_books_of("shelf-1", ["book-1", "book-2"])
    assert sorted(diff) == ["book-1", "book-2"]


# ---- update_shelf / delete_shelf -----------------------------------------


async def test_update_shelf_writes_no_edges() -> None:
    decorator, _, perm = _make_decorated()
    await decorator.update_shelf(id="shelf-1", slug="renamed")
    assert perm._store == []  # noqa: SLF001


async def test_delete_shelf_revokes_every_shelf_edge() -> None:
    """Wildcard revoke must drop every edge on the shelf, but only
    that shelf.  We seed both the storage row (so delete_shelf
    returns True) and the auth edges, then verify the survivor.
    """
    decorator, storage, perm = _make_decorated()
    # Inject a shelf row directly so the id is predictable.
    from src.db.entities.shelf import ShelfEntity  # noqa: WPS433
    storage._shelves["shelf-1"] = ShelfEntity(  # noqa: SLF001
        id="shelf-1",
        slug="s1",
        display_name=None,
        description=None,
        image_url=None,
        readme_note_id=None,
        book_ids=[],
    )
    perm._store.extend(  # noqa: SLF001
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.SHELF, "shelf-1"),
                relation=ShelfRelationEnum.OWNER,
                subject=SubjectRef(ObjectTypeEnum.USER, "owner-1"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.SHELF, "shelf-1"),
                relation=ShelfRelationEnum.READER,
                subject=SubjectRef(ObjectTypeEnum.USER, "reader-1"),
            ),
            # Unrelated shelf must NOT be touched.
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.SHELF, "shelf-other"),
                relation=ShelfRelationEnum.OWNER,
                subject=SubjectRef(ObjectTypeEnum.USER, "owner-1"),
            ),
        ]
    )

    await decorator.delete_shelf("shelf-1")

    assert all(str(r.resource.object_id) != "shelf-1" for r in perm._store)  # noqa: SLF001
    assert any(str(r.resource.object_id) == "shelf-other" for r in perm._store)  # noqa: SLF001


async def test_delete_shelf_does_not_revoke_directory_scoped_edges() -> None:
    """``directory:<book>#parent@shelf:<id>`` lives on the directory
    resource, so the shelf-scoped wildcard delete must not touch it.
    """
    decorator, _, perm = _make_decorated(seeded={"shelf-1": {"book-1"}})
    await decorator.add_book("shelf-1", "book-1")

    await decorator.delete_shelf("shelf-1")

    edges = _directory_parent_edges(perm, shelf_id="shelf-1")
    assert len(edges) == 1
    assert str(edges[0].resource.object_id) == "book-1"


async def test_delete_shelf_on_missing_shelf_returns_false_without_touching_repo() -> None:
    """No storage row -> no revoke attempt -> no repo call."""
    decorator, _, perm = _make_decorated()
    assert await decorator.delete_shelf("does-not-exist") is False
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
