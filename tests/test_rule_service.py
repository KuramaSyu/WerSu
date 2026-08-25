"""Tests for :class:`RuleServiceImpl` -- the rule service with
chain-based permission gating, payload validation and CRUD.

The tests use the in-memory rule repo + the in-memory permission
repo + a tiny fake directory facade.  The permission repo's
static implication map (``_relation_implied_permissions``) is
what makes the ``has_permission`` checks resolve to ``True``
for owner / admin users; we seed relationships explicitly so
each test can exercise the exact permission boundary it cares
about.
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.relationship import (
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.other.user_context import UserContextABC
from src.api.services.rule_service import RulePermissionError, RuleServiceError
from src.api.other.undefined import UNDEFINED
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.rule import RuleEntity
from src.services.rule_service import RuleServiceImpl
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.in_memory_rule_repo import InMemoryRuleRepo


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

    Implements only the methods the rule service and the chain
    reach for (``list_user_directory_ids``).  Every other method
    -- the abstract ones on :class:`DirectoryFacadeABC` and
    :class:`DirectoryHelperMixin` -- raises so that any future
    code path that accidentally uses them fails loudly rather
    than silently returning a fake value.
    """

    def __init__(
        self,
        viewable_directories: dict[str, List[str]] | None = None,
    ) -> None:
        # ``viewable_directories[user_id] = [directory_id, ...]``
        self._viewable = viewable_directories or {}

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        return list(self._viewable.get(user.user_id, []))

    # ---- abstract methods on the mixin (stubs) ----------------------

    async def set_parents_of(
        self,
        child_type,
        child_id: str,
        parent_type,
        parent_ids: List[str],
    ) -> None:
        raise NotImplementedError

    async def get_parents_of(
        self, child_type, child_id: str, parent_type,
    ) -> List[str]:
        raise NotImplementedError

    async def get_children_of(
        self, parent_type, parent_id: str, child_type, depth: int = 1,
    ) -> List[str]:
        raise NotImplementedError

    async def get_children_for(
        self, parent_type, parent_ids: List[str], child_type, depth: int = 1,
    ) -> dict[str, List[str]]:
        raise NotImplementedError

    async def get_parents_for(
        self, child_type, child_ids: List[str], parent_type,
    ) -> dict[str, List[str]]:
        raise NotImplementedError

    async def add_child_to(
        self, parent_type, parent_id: str, child_type, child_id: str,
    ) -> None:
        raise NotImplementedError

    async def remove_child_from(
        self, parent_type, parent_id: str, child_type, child_id: str,
    ) -> None:
        raise NotImplementedError

    # ---- abstract methods on the facade (stubs) ---------------------

    async def create_directory(
        self, entity: DirectoryEntity, user_ctx: UserContextABC,
    ) -> DirectoryEntity:
        raise NotImplementedError

    async def fetch_directory(
        self, directory_id: str, user_ctx: UserContextABC, *,
        include: object = None,
    ) -> Optional[DirectoryEntity]:
        raise NotImplementedError

    async def update_directory(
        self, entity: DirectoryEntity, user_ctx: UserContextABC,
    ) -> DirectoryEntity:
        raise NotImplementedError

    async def delete_directory(
        self, entity: DirectoryEntity, user_ctx: UserContextABC,
    ) -> None:
        raise NotImplementedError


def _note_rel(user_id: str, note_id: str, relation) -> Relationship:
    """Build a ``note#<relation>@user:<user_id>`` relationship."""
    return Relationship(
        resource=ObjectRef(object_type=ObjectTypeEnum.NOTE, object_id=note_id),
        relation=relation,
        subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id=user_id),
    )


def _dir_rel(user_id: str, dir_id: str, relation) -> Relationship:
    """Build a ``directory#<relation>@user:<user_id>`` relationship."""
    return Relationship(
        resource=ObjectRef(
            object_type=ObjectTypeEnum.DIRECTORY, object_id=dir_id,
        ),
        relation=relation,
        subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id=user_id),
    )


def _shelf_rel(user_id: str, shelf_id: str, relation) -> Relationship:
    """Build a ``shelf#<relation>@user:<user_id>`` relationship."""
    return Relationship(
        resource=ObjectRef(
            object_type=ObjectTypeEnum.SHELF, object_id=shelf_id,
        ),
        relation=relation,
        subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id=user_id),
    )


async def _service(
    *,
    viewable_directories: dict[str, List[str]] | None = None,
) -> tuple[RuleServiceImpl, InMemoryRuleRepo, InMemoryPermissionRepo, FakeDirectoryFacade]:
    rule_repo = InMemoryRuleRepo()
    perm_repo = InMemoryPermissionRepo()
    facade = FakeDirectoryFacade(
        viewable_directories=viewable_directories,
    )
    service = RuleServiceImpl(
        rule_repo=rule_repo,
        permission_repo=perm_repo,
        directory_facade=facade,
    )
    return service, rule_repo, perm_repo, facade


def _note_attached_rule(
    *, note_id: str = "n1", creator_id: str = "u-creator",
) -> RuleEntity:
    return RuleEntity(
        id=UNDEFINED,
        event_type="NoteCreated",
        attached_entity_type="note",
        attached_entity_id=note_id,
        condition={"type": "always_true"},
        action_type="add_tag",
        action_context={"tag_id": "t1"},
        enabled=True,
        creator_id=creator_id,
    )


def _dir_attached_rule(
    *, dir_id: str = "d1", creator_id: str = "u-creator",
) -> RuleEntity:
    return RuleEntity(
        id=UNDEFINED,
        event_type="NoteCreated",
        attached_entity_type="directory",
        attached_entity_id=dir_id,
        condition={"type": "always_true"},
        action_type="add_tag",
        action_context={"tag_id": "t1"},
        enabled=True,
        creator_id=creator_id,
    )


def _shelf_attached_rule(
    shelf_id: str = "s1",
    creator_id: str = "u-creator",
) -> RuleEntity:
    """Rule attached to a shelf (used by the global-rule-shaped tests)."""
    return RuleEntity(
        id=UNDEFINED,
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id=shelf_id,
        condition={"type": "always_true"},
        action_type="add_tag",
        action_context={"tag_id": "t1"},
        enabled=True,
        creator_id=creator_id,
    )


# ---- create ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_attached_rule_succeeds_for_owner():
    service, rule_repo, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    created = await service.create_rule(_note_attached_rule(note_id="n1"), _UserCtx("alice"))
    # When the rule carries a concrete ``creator_id`` the service
    # keeps it; only ``UNDEFINED`` / ``None`` triggers the
    # actor-default behaviour (see the dedicated test).
    assert created.creator_id == "u-creator"
    assert (await rule_repo.get_rule_by_id(created.id)) is not None


@pytest.mark.asyncio
async def test_create_attached_rule_succeeds_for_admin():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.ADMIN)])
    created = await service.create_rule(_note_attached_rule(), _UserCtx("alice"))
    assert created.id is not None


@pytest.mark.asyncio
async def test_create_attached_rule_rejects_non_manage_user():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.READER)])
    with pytest.raises(RulePermissionError):
        await service.create_rule(_note_attached_rule(), _UserCtx("alice"))


@pytest.mark.asyncio
async def test_create_directory_attached_rule_uses_edit_permissions():
    """Directory's relevant permission is ``edit_permissions``, not ``manage``."""
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_dir_rel("alice", "d1", "owner")])
    created = await service.create_rule(_dir_attached_rule(), _UserCtx("alice"))
    assert created.id is not None


@pytest.mark.asyncio
async def test_create_directory_attached_rule_rejects_writer_only():
    """A writer (no edit_permissions) cannot create directory rules."""
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_dir_rel("alice", "d1", "writer")])
    with pytest.raises(RulePermissionError):
        await service.create_rule(_dir_attached_rule(), _UserCtx("alice"))


@pytest.mark.asyncio
async def test_create_shelf_attached_rule_succeeds_for_owner():
    """Shelf permission is gated on ``shelf#edit_permissions``."""
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["s1"]},
    )
    await perm_repo.insert([_shelf_rel("alice", "s1", "owner")])
    created = await service.create_rule(_shelf_attached_rule(), _UserCtx("alice"))
    assert created.id is not None


@pytest.mark.asyncio
async def test_create_shelf_attached_rule_rejects_writer_only():
    """A writer (no edit_permissions) cannot create shelf rules."""
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["s1"]},
    )
    await perm_repo.insert([_shelf_rel("alice", "s1", "writer")])
    with pytest.raises(RulePermissionError):
        await service.create_rule(_shelf_attached_rule(), _UserCtx("alice"))


@pytest.mark.asyncio
async def test_create_rule_rejects_missing_attached_entity():
    """Global rules are no longer supported: a payload missing the
    attached-entity fields must raise ``ValueError`` at create time."""
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    rule = _note_attached_rule()
    rule.attached_entity_type = None
    rule.attached_entity_id = None
    with pytest.raises(ValueError):
        await service.create_rule(rule, _UserCtx("alice"))


@pytest.mark.asyncio
async def test_create_rule_defaults_creator_id_to_actor():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    rule = _note_attached_rule()
    rule.creator_id = UNDEFINED
    created = await service.create_rule(rule, _UserCtx("alice"))
    assert created.creator_id == "alice"


@pytest.mark.asyncio
async def test_create_rule_rejects_unknown_event_type():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    bad = _note_attached_rule()
    bad.event_type = "BananaCreated"
    with pytest.raises(ValueError, match="unknown event_type"):
        await service.create_rule(bad, _UserCtx("alice"))


@pytest.mark.asyncio
async def test_create_rule_rejects_invalid_condition():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    bad = _note_attached_rule()
    bad.condition = {"type": "note_content_contains"}
    with pytest.raises(ValueError, match="substring"):
        await service.create_rule(bad, _UserCtx("alice"))


@pytest.mark.asyncio
async def test_create_rule_rejects_invalid_action():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    bad = _note_attached_rule()
    bad.action_type = "add_to_directory"
    bad.action_context = {}
    with pytest.raises(ValueError, match="directory_id"):
        await service.create_rule(bad, _UserCtx("alice"))


# ---- get / list -----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rule_returns_none_for_missing():
    service, _, _, _ = await _service()
    assert await service.get_rule("missing", _UserCtx("alice")) is None


@pytest.mark.asyncio
async def test_get_rule_returns_rule_when_authorised():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    created = await service.create_rule(_note_attached_rule(), _UserCtx("alice"))
    out = await service.get_rule(created.id, _UserCtx("alice"))
    assert out is not None
    assert out.id == created.id


@pytest.mark.asyncio
async def test_get_rule_denies_non_authorised():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    created = await service.create_rule(_note_attached_rule(), _UserCtx("alice"))
    with pytest.raises(RulePermissionError):
        await service.get_rule(created.id, _UserCtx("bob"))


@pytest.mark.asyncio
async def test_list_rules_filters_by_read_permission():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1", "d2"]},
    )
    await perm_repo.insert([
        _note_rel("alice", "n1", NoteRelationEnum.OWNER),
        _note_rel("alice", "n2", NoteRelationEnum.OWNER),
    ])
    await service.create_rule(_note_attached_rule(note_id="n1"), _UserCtx("alice"))
    await service.create_rule(_note_attached_rule(note_id="n2"), _UserCtx("alice"))

    # Bob is a reader on n1 -> no manage -> sees no rules.
    await perm_repo.insert([_note_rel("bob", "n1", NoteRelationEnum.READER)])
    rules_bob = await service.list_rules(_UserCtx("bob"))
    assert rules_bob == []

    rules_alice = await service.list_rules(_UserCtx("alice"))
    assert len(rules_alice) == 2
    assert {r.attached_entity_id for r in rules_alice} == {"n1", "n2"}


@pytest.mark.asyncio
async def test_list_rules_includes_shelf_rule_when_user_owns_shelf():
    # Both alice and admin own the shelf; alice creates the rule,
    # admin sees it in the list (manage permission on shelf).
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["s1"], "admin": ["s1"]},
    )
    await perm_repo.insert([
        _shelf_rel("alice", "s1", "owner"),
        _shelf_rel("admin", "s1", "owner"),
    ])
    created = await service.create_rule(_shelf_attached_rule(), _UserCtx("alice"))
    rules = await service.list_rules(_UserCtx("admin"))
    assert any(r.id == created.id for r in rules)


# ---- update / delete ------------------------------------------------------


@pytest.mark.asyncio
async def test_update_rule_persists_changes():
    service, rule_repo, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    created = await service.create_rule(_note_attached_rule(), _UserCtx("alice"))
    updated = await service.update_rule(
        RuleEntity(id=created.id, enabled=False),
        _UserCtx("alice"),
    )
    assert updated.enabled is False
    fetched = await rule_repo.get_rule_by_id(created.id)
    assert fetched is not None
    assert fetched.enabled is False


@pytest.mark.asyncio
async def test_update_rule_rejects_invalid_event_type():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    created = await service.create_rule(_note_attached_rule(), _UserCtx("alice"))
    with pytest.raises(ValueError, match="unknown event_type"):
        await service.update_rule(
            RuleEntity(id=created.id, event_type="BananaCreated"),
            _UserCtx("alice"),
        )


@pytest.mark.asyncio
async def test_update_rule_404s():
    service, _, _, _ = await _service()
    with pytest.raises(ValueError, match="rule not found"):
        await service.update_rule(
            RuleEntity(id="missing", enabled=False),
            _UserCtx("alice"),
        )


@pytest.mark.asyncio
async def test_update_rule_denies_non_manage_user():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    created = await service.create_rule(_note_attached_rule(), _UserCtx("alice"))
    with pytest.raises(RulePermissionError):
        await service.update_rule(
            RuleEntity(id=created.id, enabled=False),
            _UserCtx("bob"),
        )


@pytest.mark.asyncio
async def test_delete_rule_succeeds_for_owner():
    service, rule_repo, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    created = await service.create_rule(_note_attached_rule(), _UserCtx("alice"))
    await service.delete_rule(created.id, _UserCtx("alice"))
    assert await rule_repo.get_rule_by_id(created.id) is None


@pytest.mark.asyncio
async def test_delete_rule_denies_non_manage_user():
    service, _, perm_repo, _ = await _service(
        viewable_directories={"alice": ["d1"]},
    )
    await perm_repo.insert([_note_rel("alice", "n1", NoteRelationEnum.OWNER)])
    created = await service.create_rule(_note_attached_rule(), _UserCtx("alice"))
    with pytest.raises(RulePermissionError):
        await service.delete_rule(created.id, _UserCtx("bob"))


@pytest.mark.asyncio
async def test_delete_rule_404s():
    service, _, _, _ = await _service()
    with pytest.raises(ValueError, match="rule not found"):
        await service.delete_rule("missing", _UserCtx("alice"))
