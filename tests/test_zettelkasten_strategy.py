"""Idempotency tests for ZettelkastenStrategy."""

from __future__ import annotations

from typing import List

from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.undefined import UNDEFINED, unwrap_undefined
from src.api.other.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.rule import RuleEntity
from src.db.entities.shelf import ShelfEntity
from src.services.shelf_bootstrap import (
    ZettelkastenStrategy,
    build_strategy,
    ensure_default_fleeting_rule,
)
from src.services.shelf_bootstrap.registry import STRATEGIES
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.in_memory_rule_repo import InMemoryRuleRepo
from tests.stubs.in_memory_shelf_repo import InMemoryShelfRepo


class _UserCtx(UserContextABC):
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
    def __init__(self) -> None:
        self._counter = 0
        self.created: List[DirectoryEntity] = []
        self._by_id = {}

    def _mint_id(self) -> str:
        self._counter += 1
        return f"dir-{self._counter}"

    async def create_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: UserContextABC = None,
    ) -> DirectoryEntity:
        new_id = self._mint_id()
        created = DirectoryEntity(
            id=new_id,
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
        )
        self.created.append(created)
        self._by_id[new_id] = created
        return created

    async def fetch_directories_by_ids(
        self, ids: List[str],
    ) -> List[DirectoryEntity]:
        out: List[DirectoryEntity] = []
        for did in ids:
            d = self._by_id.get(str(did))
            if d is not None:
                out.append(d)
        return out

    # Unused abstract stubs - raise loudly if accidentally hit.
    async def fetch_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def update_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_directory(self, *args, **kwargs) -> bool:
        raise NotImplementedError

    async def list_user_directory_ids(self, *args, **kwargs):
        raise NotImplementedError

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

    async def fetch_all_directories(self, *args, **kwargs):
        raise NotImplementedError

    async def resolve_files_of_directory(self, *args, **kwargs):
        raise NotImplementedError

    async def resolve_subtree(self, *args, **kwargs):
        raise NotImplementedError


def _make_strategy():
    """Build a strategy with the in-memory test repos."""
    shelf_repo = InMemoryShelfRepo()
    rule_repo = InMemoryRuleRepo()
    directory_facade = FakeDirectoryFacade()
    permission_repo = InMemoryPermissionRepo()
    strategy = ZettelkastenStrategy(
        shelf_repo=shelf_repo,
        rule_repo=rule_repo,
        directory_facade=directory_facade,
        permission_repo=permission_repo,
    )
    return strategy, shelf_repo, rule_repo, directory_facade


async def _make_shelf(shelf_repo: InMemoryShelfRepo) -> ShelfEntity:
    """Insert a real shelf row; the strategy needs a real id to probe."""
    return await shelf_repo.insert_shelf(
        slug="my shelf", display_name="My Shelf",
    )


def _shelf_id(shelf: ShelfEntity) -> str:
    return str(unwrap_undefined(shelf.id))


# ---- happy path -----------------------------------------------------------


async def test_apply_creates_three_default_books_and_a_rule() -> None:
    """First apply on an empty shelf creates all three books + a rule."""
    strategy, shelf_repo, rule_repo, facade = _make_strategy()
    shelf = await _make_shelf(shelf_repo)
    sid = _shelf_id(shelf)

    result = await strategy.apply(
        shelf=shelf, owner_id="user-1", user_ctx=_UserCtx("user-1"),
    )

    assert len(facade.created) == 3
    assert {d.slug for d in facade.created} == {
        "fleeting_notes", "literature_notes", "permanent_notes",
    }
    assert len(result.created_directory_ids) == 3
    assert result.created_rule_id is not None
    bound = await shelf_repo.get_books_of(sid)
    assert len(bound) == 3
    rules = await rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id=sid,
    )
    assert len(rules) == 1
    assert rules[0].action_context["directory_id"] in bound


# ---- idempotency on books -------------------------------------------------


async def test_apply_skips_books_that_already_exist_by_slug() -> None:
    """Re-runs against a fully-bootstrapped shelf create no duplicates."""
    strategy, shelf_repo, _, facade = _make_strategy()
    shelf = await _make_shelf(shelf_repo)
    sid = _shelf_id(shelf)

    await strategy.apply(
        shelf=shelf, owner_id="user-1", user_ctx=_UserCtx("user-1"),
    )
    assert len(facade.created) == 3
    bound_after_first = await shelf_repo.get_books_of(sid)

    result = await strategy.apply(
        shelf=shelf, owner_id="user-1", user_ctx=_UserCtx("user-1"),
    )
    assert len(facade.created) == 3, "second apply must not duplicate books"
    assert result.created_directory_ids == []
    bound_after_second = await shelf_repo.get_books_of(sid)
    assert bound_after_second == bound_after_first


async def test_apply_creates_only_missing_default_books() -> None:
    """Only the missing default slugs get created on a re-apply."""
    strategy, shelf_repo, _, facade = _make_strategy()
    shelf = await _make_shelf(shelf_repo)
    sid = _shelf_id(shelf)

    # Pre-seed: shelf already has fleeting_notes.
    fleeting = DirectoryEntity(
        id="dir-existing-fleeting",
        slug="fleeting_notes",
        display_name="Existing Fleeting",
    )
    facade._by_id[fleeting.id] = fleeting  # type: ignore[attr-defined]
    await shelf_repo.add_book(sid, str(fleeting.id))

    result = await strategy.apply(
        shelf=shelf, owner_id="user-1", user_ctx=_UserCtx("user-1"),
    )

    created_slugs = {d.slug for d in facade.created}
    assert created_slugs == {"literature_notes", "permanent_notes"}
    assert len(result.created_directory_ids) == 2
    bound = await shelf_repo.get_books_of(sid)
    assert str(fleeting.id) in bound
    assert len(bound) == 3


# ---- idempotency on rule -------------------------------------------------


async def test_apply_does_not_recreate_rule_when_one_exists() -> None:
    """A pre-existing rule is preserved; no duplicate is inserted."""
    strategy, shelf_repo, rule_repo, _ = _make_strategy()
    shelf = await _make_shelf(shelf_repo)
    sid = _shelf_id(shelf)

    first = await strategy.apply(
        shelf=shelf, owner_id="user-1", user_ctx=_UserCtx("user-1"),
    )
    rules_after_first = await rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id=sid,
    )
    assert len(rules_after_first) == 1
    assert first.created_rule_id is not None

    second = await strategy.apply(
        shelf=shelf, owner_id="user-1", user_ctx=_UserCtx("user-1"),
    )
    rules_after_second = await rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id=sid,
    )
    assert len(rules_after_second) == 1, "second apply must not duplicate the rule"
    assert second.created_rule_id == first.created_rule_id


async def test_apply_inserts_rule_when_only_non_default_books_bound() -> None:
    """Non-default books on the shelf are preserved; rule is inserted
    because the strategy creates the missing fleeting book."""
    strategy, shelf_repo, rule_repo, facade = _make_strategy()
    shelf = await _make_shelf(shelf_repo)
    sid = _shelf_id(shelf)

    custom = DirectoryEntity(
        id="dir-custom",
        slug="custom_notes",
        display_name="Custom Notes",
    )
    facade._by_id[custom.id] = custom  # type: ignore[attr-defined]
    await shelf_repo.add_book(sid, str(custom.id))

    result = await strategy.apply(
        shelf=shelf, owner_id="user-1", user_ctx=_UserCtx("user-1"),
    )

    created_slugs = {d.slug for d in facade.created}
    assert created_slugs == {
        "fleeting_notes", "literature_notes", "permanent_notes",
    }
    bound = await shelf_repo.get_books_of(sid)
    assert str(custom.id) in bound
    assert len(bound) == 4

    rules = await rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id=sid,
    )
    assert len(rules) == 1
    assert rules[0].action_context["directory_id"] in bound
    assert result.created_rule_id == str(unwrap_undefined(rules[0].id))


# ---- ensure_default_fleeting_rule helper --------------------------------


async def test_ensure_rule_helper_returns_existing_rule_id() -> None:
    _, shelf_repo, rule_repo, _ = _make_strategy()
    await shelf_repo.insert_shelf(slug="s")

    await rule_repo.create_rule(
        RuleEntity(
            id=UNDEFINED,
            event_type="NoteCreated",
            attached_entity_type="shelf",
            attached_entity_id="shelf-1",
            condition={"type": "always_true"},
            action_type="add_to_directory",
            action_context={"directory_id": "dir-fleeting"},
            enabled=True,
            creator_id="user-1",
        ),
    )

    rule_id, kept = await ensure_default_fleeting_rule(
        rule_repo=rule_repo,
        shelf_id="shelf-1",
        owner_id="user-1",
        fleeting_directory_id="dir-fleeting",
    )
    assert rule_id is not None
    assert kept is True
    rules = await rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id="shelf-1",
    )
    assert len(rules) == 1


async def test_ensure_rule_helper_inserts_when_missing() -> None:
    _, shelf_repo, rule_repo, _ = _make_strategy()

    rule_id, kept = await ensure_default_fleeting_rule(
        rule_repo=rule_repo,
        shelf_id="shelf-1",
        owner_id="user-1",
        fleeting_directory_id="dir-fleeting",
    )
    assert rule_id is not None
    assert kept is False
    rules = await rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id="shelf-1",
    )
    assert len(rules) == 1
    assert rules[0].action_context["directory_id"] == "dir-fleeting"
    assert rules[0].creator_id == "user-1"


async def test_ensure_rule_helper_no_op_without_fleeting_target() -> None:
    _, shelf_repo, rule_repo, _ = _make_strategy()

    rule_id, kept = await ensure_default_fleeting_rule(
        rule_repo=rule_repo,
        shelf_id="shelf-1",
        owner_id="user-1",
        fleeting_directory_id=None,
    )
    assert rule_id is None
    assert kept is True
    rules = await rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id="shelf-1",
    )
    assert rules == []


# ---- build_strategy / registry wiring -----------------------------------


async def test_build_strategy_returns_bound_zettelkasten() -> None:
    strategy = build_strategy(
        "zettelkasten",
        shelf_repo=InMemoryShelfRepo(),
        rule_repo=InMemoryRuleRepo(),
        directory_facade=FakeDirectoryFacade(),
        permission_repo=InMemoryPermissionRepo(),
    )
    assert isinstance(strategy, ZettelkastenStrategy)
    assert strategy.name == "zettelkasten"


def test_registry_contains_zettelkasten_factory() -> None:
    assert "zettelkasten" in STRATEGIES
    assert callable(STRATEGIES["zettelkasten"])