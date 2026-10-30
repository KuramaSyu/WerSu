"""Tests for :class:`InMemoryRuleRepo` (the test fake used by the
rule service / dispatcher / gRPC adapter tests)."""

from __future__ import annotations

import pytest

from src.api.other.undefined import UNDEFINED
from tests.doubles import InMemoryRuleRepo
from src.db.entities.rule import RuleEntity


def _make(
    *,
    event_type: str = "NoteCreated",
    attached_entity_type=None,
    attached_entity_id=None,
    condition: dict | None = None,
    action_type: str = "add_to_directory",
    action_context: dict | None = None,
    enabled: bool = True,
    creator_id: str = "u1",
) -> RuleEntity:
    return RuleEntity(
        id=UNDEFINED,
        event_type=event_type,
        attached_entity_type=attached_entity_type,
        attached_entity_id=attached_entity_id,
        condition=condition or {"type": "always_true"},
        action_type=action_type,
        action_context=action_context or {"directory_id": "d1"},
        enabled=enabled,
        creator_id=creator_id,
    )


async def test_create_rule_assigns_id_and_timestamps():
    repo = InMemoryRuleRepo()
    rule = await repo.create_rule(_make())
    assert rule.id is not None
    assert rule.created_at is not None
    assert rule.updated_at is not None


async def test_create_rule_validates_required_fields():
    repo = InMemoryRuleRepo()
    bad = RuleEntity(
        id=UNDEFINED,
        event_type=UNDEFINED,
        condition=UNDEFINED,
        action_type=UNDEFINED,
        action_context=UNDEFINED,
        enabled=UNDEFINED,
        creator_id=UNDEFINED,
    )
    with pytest.raises(ValueError, match="event_type is required"):
        await repo.create_rule(bad)


async def test_get_rule_by_id_returns_none_for_missing():
    repo = InMemoryRuleRepo()
    assert await repo.get_rule_by_id("missing") is None


async def test_update_rule_requires_id():
    repo = InMemoryRuleRepo()
    with pytest.raises(ValueError, match="id is required"):
        await repo.update_rule(RuleEntity(id=UNDEFINED, enabled=False))


async def test_update_rule_404s_on_missing():
    repo = InMemoryRuleRepo()
    with pytest.raises(ValueError, match="rule not found"):
        await repo.update_rule(RuleEntity(id="missing", enabled=False))


async def test_update_rule_bumps_updated_at():
    repo = InMemoryRuleRepo()
    created = await repo.create_rule(_make())
    original_updated = created.updated_at
    updated = await repo.update_rule(
        RuleEntity(id=created.id, enabled=False),
    )
    assert updated.enabled is False
    assert updated.updated_at >= original_updated
    # created_at must not move
    assert updated.created_at == created.created_at


async def test_delete_rule_404s_on_missing():
    repo = InMemoryRuleRepo()
    with pytest.raises(ValueError, match="rule not found"):
        await repo.delete_rule("missing")


async def test_delete_rule_removes():
    repo = InMemoryRuleRepo()
    created = await repo.create_rule(_make())
    await repo.delete_rule(created.id)
    assert await repo.get_rule_by_id(created.id) is None


async def test_list_rules_filters_by_event_type():
    repo = InMemoryRuleRepo()
    await repo.create_rule(_make(event_type="NoteCreated"))
    await repo.create_rule(_make(event_type="NoteUpdated"))
    out = await repo.list_rules(event_type="NoteCreated")
    assert len(out) == 1
    assert out[0].event_type == "NoteCreated"


async def test_list_rules_filters_by_enabled_only():
    repo = InMemoryRuleRepo()
    await repo.create_rule(_make(enabled=True))
    await repo.create_rule(_make(enabled=False))
    out = await repo.list_rules(enabled_only=True)
    assert len(out) == 1
    assert out[0].enabled is True


async def test_list_rules_filters_by_attached_entity():
    repo = InMemoryRuleRepo()
    await repo.create_rule(_make(
        attached_entity_type="directory",
        attached_entity_id="d1",
    ))
    await repo.create_rule(_make(
        attached_entity_type="directory",
        attached_entity_id="d2",
    ))
    out = await repo.list_rules(
        attached_entity_type="directory",
        attached_entity_id="d1",
    )
    assert len(out) == 1
    assert out[0].attached_entity_id == "d1"


async def test_list_rules_filters_by_creator_id():
    repo = InMemoryRuleRepo()
    await repo.create_rule(_make(creator_id="alice"))
    await repo.create_rule(_make(creator_id="bob"))
    out = await repo.list_rules(creator_id="alice")
    assert len(out) == 1
    assert out[0].creator_id == "alice"


async def test_list_rules_for_event_only_returns_enabled():
    repo = InMemoryRuleRepo()
    await repo.create_rule(_make(event_type="NoteCreated", enabled=True))
    await repo.create_rule(_make(event_type="NoteCreated", enabled=False))
    await repo.create_rule(_make(event_type="NoteUpdated", enabled=True))
    out = await repo.list_rules_for_event("NoteCreated")
    assert len(out) == 1
    assert out[0].event_type == "NoteCreated"
    assert out[0].enabled is True
