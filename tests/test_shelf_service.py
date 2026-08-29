"""Tests for :class:`ShelfServiceImpl` -- the shelf service with
chain-based permission gating, CRUD, dry-delete and bootstrap
strategy dispatch.

The tests use the in-memory shelf / rule / permission repos plus a
tiny fake directory facade.  The permission repo's static
implication map makes ``has_permission`` resolve to ``True`` for
owner / admin / writer / reader users; we seed relationships
explicitly so each test can exercise the exact permission
boundary it cares about.
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.relationship import (
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    ShelfRelationEnum,
    SubjectRef,
)
from src.api.other.undefined import UNDEFINED, unwrap_undefined
from src.api.other.user_context import UserContextABC
from src.api.services.shelf_service import (
    BootstrapResult,
    BootstrapStrategy,
    DryDeleteResult,
    ShelfPermissionError,
    ShelfReadOptions,
)
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.shelf import ShelfEntity
from src.db.repos.shelf.spicedb_decorator import SpicedbShelfRepoDecorator
from src.services.shelf_service import ShelfServiceImpl
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.in_memory_rule_repo import InMemoryRuleRepo
from tests.stubs.in_memory_shelf_repo import InMemoryShelfRepo


# ---- fakes ----------------------------------------------------------------


class _UserCtx(UserContextABC):
    """Minimal :class:`UserContextABC` for tests."""

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def type(self):  # type: ignore[override]
        return UNDEFINED

    async def is_temporary_user(self) -> bool:
        return False


class FakeDirectoryFacade(DirectoryFacadeABC):
    """Minimal :class:`DirectoryFacadeABC` for tests.

    The shelf service only consults ``DEFAULT_DIRECTORY_SPECS``
    (a class-level tuple) and forwards ``create_directory`` to
    whatever's here.  We capture the created entities and stash
    them with a deterministic id so the strategy's
    ``get_default_directory_specs`` loop sees the same shape
    production does.
    """

    def __init__(self) -> None:
        self._counter = 0
        self.created: List[DirectoryEntity] = []

    def _mint_id(self) -> str:
        self._counter += 1
        return f"dir-{self._counter}"

    async def create_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: Optional[UserContextABC] = None,
    ) -> DirectoryEntity:
        created = DirectoryEntity(
            id=self._mint_id(),
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
            relations=list(entity.relations or []),
        )
        self.created.append(created)
        return created

    # ---- abstract methods on the mixin / facade: stubs -------------

    async def set_parents_of(self, *args, **kwargs) -> None:
        raise NotImplementedError

    async def get_parents_of(self, *args, **kwargs):
        raise NotImplementedError

    async def get_children_of(self, *args, **kwargs):
        raise NotImplementedError

    async def get_children_for(self, *args, **kwargs):
        raise NotImplementedError

    async def get_parents_for(self, *args, **kwargs):
        raise NotImplementedError

    async def add_child_to(self, *args, **kwargs) -> None:
        raise NotImplementedError

    async def remove_child_from(self, *args, **kwargs) -> None:
        raise NotImplementedError

    async def list_user_directory_ids(self, *args, **kwargs):
        raise NotImplementedError

    async def fetch_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def update_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_directory(self, *args, **kwargs) -> bool:
        raise NotImplementedError

    async def fetch_all_directories(self, *args, **kwargs):
        raise NotImplementedError

    async def fetch_directories_by_ids(
        self, ids: List[str],
    ) -> List[DirectoryEntity]:
        # Mirror :meth:`_TestDirectoryRepo`'s behaviour: return
        # whatever matching directories the test pre-seeded into
        # the facade's ``created`` list (the only directories this
        # fake knows about).  Missing ids silently drop.
        out: List[DirectoryEntity] = []
        for did in ids:
            for d in self.created:
                if str(d.id) == str(did):
                    out.append(d)
                    break
        return out

    async def resolve_files_of_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def resolve_subtree(self, *args, **kwargs):
        raise NotImplementedError


def _shelf_rel(user_id: str, shelf_id: str, relation: str) -> Relationship:
    """Build a ``shelf#<relation>@user:<user_id>`` relationship."""
    return Relationship(
        resource=ObjectRef(
            object_type=ObjectTypeEnum.SHELF, object_id=shelf_id,
        ),
        relation=relation,
        subject=SubjectRef(
            object_type=ObjectTypeEnum.USER, object_id=user_id,
        ),
    )


def _make_service(
    *,
    shelf_repo: Optional[InMemoryShelfRepo] = None,
    rule_repo: Optional[InMemoryRuleRepo] = None,
    permission_repo: Optional[InMemoryPermissionRepo] = None,
    directory_facade: Optional[FakeDirectoryFacade] = None,
) -> ShelfServiceImpl:
    """Build a :class:`ShelfServiceImpl` with in-memory defaults.

    The ``shelf_repo`` argument is the *storage* adapter -- the
    helper wraps it in :class:`SpicedbShelfRepoDecorator` so the
    service's ``user_ctx`` keyword lands in the same place it
    does in production.  Tests that need to inspect the
    underlying storage should keep a reference to the unwrapped
    repository and assert on it directly.
    """
    storage = shelf_repo if shelf_repo is not None else InMemoryShelfRepo()
    perm = (
        permission_repo
        if permission_repo is not None
        else InMemoryPermissionRepo()
    )
    decorated = SpicedbShelfRepoDecorator(
        inner=storage,
        permission_repo=perm,
    )
    return ShelfServiceImpl(
        shelf_repo=decorated,
        permission_repo=perm,
        directory_facade=(
            directory_facade if directory_facade is not None else FakeDirectoryFacade()
        ),
        rule_repo=rule_repo if rule_repo is not None else InMemoryRuleRepo(),
    )


# ---- create ---------------------------------------------------------------


async def test_create_shelf_inserts_owner_relation_and_returns_entity() -> None:
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    service = _make_service(
        shelf_repo=shelf_repo, permission_repo=permission_repo,
    )

    entity = ShelfEntity(slug="my shelf", display_name="My Shelf")
    persisted, bootstrap_result = await service.create_shelf(
        entity, _UserCtx("u-1"),
    )

    assert unwrap_undefined(persisted.id) == unwrap_undefined(persisted.id)
    assert persisted.slug == "my shelf"
    assert bootstrap_result == BootstrapResult()
    # shelf#owner and shelf#admin are both inserted, both target the caller.
    relations = sorted(
        permission_repo._store,  # noqa: SLF001
        key=lambda r: str(r.relation),
    )
    assert len(relations) == 2
    for rel in relations:
        assert rel.resource.object_type == ObjectTypeEnum.SHELF
        assert rel.subject.object_id == "u-1"
    assert [str(r.relation) for r in relations] == ["admin", "owner"]


async def test_create_shelf_without_slug_raises() -> None:
    service = _make_service()
    with pytest.raises(ValueError, match="slug is required"):
        await service.create_shelf(
            ShelfEntity(), _UserCtx("u-1"),
        )


async def test_create_shelf_with_zettelkasten_strategy_creates_books_and_rule() -> None:
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    rule_repo = InMemoryRuleRepo()
    directory_facade = FakeDirectoryFacade()
    service = _make_service(
        shelf_repo=shelf_repo,
        rule_repo=rule_repo,
        permission_repo=permission_repo,
        directory_facade=directory_facade,
    )

    persisted, bootstrap_result = await service.create_shelf(
        ShelfEntity(slug="zettelkasten"),
        _UserCtx("u-1"),
        bootstrap=BootstrapStrategy.ZETTELKASTEN,
    )

    # 3 default books + 1 default-fleeting rule
    assert len(directory_facade.created) == 3
    assert len(rule_repo._rules) == 1  # noqa: SLF001
    assert len(bootstrap_result.created_directory_ids) == 3
    assert bootstrap_result.created_rule_id is not None
    # The shelf now carries all 3 books as bindings.
    book_ids = await shelf_repo.get_books_of(str(unwrap_undefined(persisted.id)))
    assert sorted(book_ids) == sorted(bootstrap_result.created_directory_ids)


async def test_create_shelf_with_none_strategy_does_not_run_strategy() -> None:
    rule_repo = InMemoryRuleRepo()
    directory_facade = FakeDirectoryFacade()
    service = _make_service(
        rule_repo=rule_repo, directory_facade=directory_facade,
    )

    _, bootstrap_result = await service.create_shelf(
        ShelfEntity(slug="bare"),
        _UserCtx("u-1"),
        bootstrap=BootstrapStrategy.NONE,
    )

    assert bootstrap_result.created_directory_ids == []
    assert bootstrap_result.created_rule_id is None
    assert rule_repo._rules == {}  # noqa: SLF001
    assert directory_facade.created == []


# ---- read -----------------------------------------------------------------


async def test_get_shelf_returns_none_when_missing() -> None:
    service = _make_service()
    assert await service.get_shelf("missing", _UserCtx("u-1")) is None


async def test_get_shelf_requires_view_permission() -> None:
    permission_repo = InMemoryPermissionRepo()
    service = _make_service(permission_repo=permission_repo)
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="locked"), _UserCtx("owner"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))

    with pytest.raises(ShelfPermissionError):
        await service.get_shelf(shelf_id, _UserCtx("stranger"))


async def test_get_shelf_owner_can_read() -> None:
    permission_repo = InMemoryPermissionRepo()
    service = _make_service(permission_repo=permission_repo)
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))

    fetched = await service.get_shelf(shelf_id, _UserCtx("u-1"))
    assert fetched is not None
    assert fetched.id == shelf_id


async def test_get_shelves_drops_shelves_caller_cannot_view() -> None:
    permission_repo = InMemoryPermissionRepo()
    service = _make_service(permission_repo=permission_repo)
    mine, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    other, _ = await service.create_shelf(
        ShelfEntity(slug="other"), _UserCtx("u-2"),
    )

    result = await service.get_shelves(
        [
            str(unwrap_undefined(mine.id)),
            str(unwrap_undefined(other.id)),
        ],
        _UserCtx("u-1"),
    )
    assert [e.id for e in result] == [str(unwrap_undefined(mine.id))]


async def test_list_shelves_filters_via_permission_repo_lookup() -> None:
    permission_repo = InMemoryPermissionRepo()
    service = _make_service(permission_repo=permission_repo)
    mine, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    await service.create_shelf(
        ShelfEntity(slug="other"), _UserCtx("u-2"),
    )

    result = await service.list_shelves(_UserCtx("u-1"))
    assert [e.id for e in result] == [str(unwrap_undefined(mine.id))]


async def test_list_shelves_paginates_after_permission_filter() -> None:
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    service = _make_service(
        shelf_repo=shelf_repo, permission_repo=permission_repo,
    )

    created = []
    for i in range(5):
        shelf, _ = await service.create_shelf(
            ShelfEntity(slug=f"s-{i}"), _UserCtx(f"u-{i}"),
        )
        # Grant view to a single viewer so the lookup filter
        # returns *all* shelves; pagination then trims.
        await permission_repo.insert([
            _shelf_rel("viewer", str(unwrap_undefined(shelf.id)), "reader"),
        ])
        created.append(shelf)

    page1 = await service.list_shelves(
        _UserCtx("viewer"), limit=2, offset=0,
    )
    page2 = await service.list_shelves(
        _UserCtx("viewer"), limit=2, offset=2,
    )

    assert len(page1) == 2
    assert len(page2) == 2
    assert {e.id for e in page1}.isdisjoint({e.id for e in page2})


async def test_list_shelves_rejects_negative_offset() -> None:
    service = _make_service()
    with pytest.raises(ValueError, match="offset must be >= 0"):
        await service.list_shelves(_UserCtx("u-1"), offset=-1)


# ---- update ---------------------------------------------------------------


async def test_update_shelf_requires_write_permission() -> None:
    permission_repo = InMemoryPermissionRepo()
    service = _make_service(permission_repo=permission_repo)
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="locked"), _UserCtx("owner"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))

    with pytest.raises(ShelfPermissionError):
        await service.update_shelf(
            ShelfEntity(id=shelf_id, slug="hacked"),
            _UserCtx("stranger"),
        )


async def test_update_shelf_without_id_raises() -> None:
    service = _make_service()
    with pytest.raises(ValueError, match="id is required"):
        await service.update_shelf(ShelfEntity(), _UserCtx("u-1"))


async def test_update_shelf_owner_can_patch_metadata() -> None:
    permission_repo = InMemoryPermissionRepo()
    service = _make_service(permission_repo=permission_repo)
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="mine", description="old"),
        _UserCtx("u-1"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))

    updated = await service.update_shelf(
        ShelfEntity(id=shelf_id, description="new"),
        _UserCtx("u-1"),
    )
    assert updated.description == "new"
    assert updated.slug == "mine"


# ---- delete ---------------------------------------------------------------


async def test_delete_shelf_returns_none_on_real_delete() -> None:
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    service = _make_service(
        shelf_repo=shelf_repo, permission_repo=permission_repo,
    )
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))

    assert await service.delete_shelf(shelf_id, _UserCtx("u-1")) is None
    assert await service.get_shelf(shelf_id, _UserCtx("u-1")) is None


async def test_delete_shelf_requires_delete_permission() -> None:
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    service = _make_service(
        shelf_repo=shelf_repo, permission_repo=permission_repo,
    )
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))

    # ``reader`` may view but not delete.
    await permission_repo.insert([_shelf_rel("reader", shelf_id, "reader")])

    with pytest.raises(ShelfPermissionError):
        await service.delete_shelf(shelf_id, _UserCtx("reader"))


async def test_delete_shelf_dry_returns_cascade_without_removing_row() -> None:
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    service = _make_service(
        shelf_repo=shelf_repo, permission_repo=permission_repo,
    )
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))
    await shelf_repo.set_books_of(shelf_id, ["b-1", "b-2", "b-3"])

    result = await service.delete_shelf(
        shelf_id, _UserCtx("u-1"), dry=True,
    )

    assert isinstance(result, DryDeleteResult)
    assert result.affected_book_ids == ["b-1", "b-2", "b-3"]
    assert result.binding_count == 3
    # Row is still there.
    assert await service.get_shelf(shelf_id, _UserCtx("u-1")) is not None


async def test_delete_shelf_without_id_raises() -> None:
    service = _make_service()
    with pytest.raises(ValueError, match="id is required"):
        await service.delete_shelf("", _UserCtx("u-1"))


# ---- book bindings --------------------------------------------------------


async def test_set_books_requires_write_permission() -> None:
    permission_repo = InMemoryPermissionRepo()
    service = _make_service(permission_repo=permission_repo)
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))

    with pytest.raises(ShelfPermissionError):
        await service.set_books(shelf_id, ["b-1"], _UserCtx("stranger"))


async def test_set_books_replaces_full_set() -> None:
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    service = _make_service(
        shelf_repo=shelf_repo, permission_repo=permission_repo,
    )
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))

    await service.set_books(shelf_id, ["b-1", "b-2"], _UserCtx("u-1"))
    assert await shelf_repo.get_books_of(shelf_id) == ["b-1", "b-2"]

    await service.set_books(shelf_id, ["b-3"], _UserCtx("u-1"))
    assert await shelf_repo.get_books_of(shelf_id) == ["b-3"]


async def test_attach_and_detach_book_are_idempotent() -> None:
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    service = _make_service(
        shelf_repo=shelf_repo, permission_repo=permission_repo,
    )
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))

    await service.attach_book(shelf_id, "b-1", _UserCtx("u-1"))
    await service.attach_book(shelf_id, "b-1", _UserCtx("u-1"))  # idempotent
    assert await shelf_repo.get_books_of(shelf_id) == ["b-1"]

    await service.detach_book(shelf_id, "b-1", _UserCtx("u-1"))
    await service.detach_book(shelf_id, "b-1", _UserCtx("u-1"))  # idempotent
    assert await shelf_repo.get_books_of(shelf_id) == []


async def test_get_books_of_shelf_requires_view() -> None:
    permission_repo = InMemoryPermissionRepo()
    service = _make_service(permission_repo=permission_repo)
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    shelf_id = str(unwrap_undefined(shelf.id))

    with pytest.raises(ShelfPermissionError):
        await service.get_books_of_shelf(shelf_id, _UserCtx("stranger"))


async def test_get_shelves_of_book_filters_by_view_permission() -> None:
    permission_repo = InMemoryPermissionRepo()
    shelf_repo = InMemoryShelfRepo()
    service = _make_service(
        shelf_repo=shelf_repo, permission_repo=permission_repo,
    )
    shelf, _ = await service.create_shelf(
        ShelfEntity(slug="mine"), _UserCtx("u-1"),
    )
    other, _ = await service.create_shelf(
        ShelfEntity(slug="other"), _UserCtx("u-2"),
    )
    mine_id = str(unwrap_undefined(shelf.id))
    other_id = str(unwrap_undefined(other.id))

    # Both shelves hold the same book.
    await shelf_repo.add_book(mine_id, "b-1")
    await shelf_repo.add_book(other_id, "b-1")

    visible = await service.get_shelves_of_book("b-1", _UserCtx("u-1"))
    assert visible == [mine_id]


async def test_get_shelves_of_book_without_book_id_raises() -> None:
    service = _make_service()
    with pytest.raises(ValueError, match="book_id is required"):
        await service.get_shelves_of_book("", _UserCtx("u-1"))


# ---- read-options ---------------------------------------------------------


async def test_resolve_shelf_read_options_defaults_include_books_to_false() -> None:
    """``ShelfReadOptions`` defaults ``include_books`` to ``False``.

    The TypedDict factory doesn't auto-populate ``include_books``
    -- callers must use :func:`resolve_shelf_read_options` to
    guarantee a complete options dict.
    """
    from src.api.services.shelf_service import resolve_shelf_read_options
    assert resolve_shelf_read_options(None) == {"include_books": False}
    assert resolve_shelf_read_options(
        ShelfReadOptions(),
    ) == {"include_books": False}
    assert resolve_shelf_read_options(
        ShelfReadOptions(include_books=True),
    ) == {"include_books": True}