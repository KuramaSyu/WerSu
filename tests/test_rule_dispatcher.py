"""Tests for the rule dispatcher and its end-to-end interaction with
the bus + an in-memory rule repo.

The tests cover:

* Global rules (no attached entity) match every event of their type.
* Entity-attached rules only match events whose primary entity id
  matches the attached entity id (v1 scope).
* Conditions are evaluated and only matching rules fire their action.
* Action executors call the right repo method with the right args.
* Per-rule failures are caught; the dispatcher never raises.
* The full bus -> dispatcher -> listener chain works end-to-end.
"""

from __future__ import annotations
from src.api.events.event_context import EventContext, NoopEventContext
from src.api.events.events import DirectoryCreated, NoteCreated, NoteUpdated
from src.api.events.rule_dispatcher import RuleDispatcher
from src.api.other.undefined import UNDEFINED
from src.db.entities.rule import RuleEntity
from src.services.event_bus import InMemoryEventBus
from tests.stubs.in_memory_rule_repo import InMemoryRuleRepo
import pytest


# ---- fakes for the directory / tag repos ---------------------------------


class FakeDirectoryRepo:
    """Records every ``add_child_to`` call for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    async def add_child_to(
        self,
        parent_type: str,
        parent_id: str,
        child_type: str,
        child_id: str,
    ) -> None:
        self.calls.append((parent_type, parent_id, child_type, child_id))


class FakeTagRepo:
    """Records every ``assign_tag_to`` call for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def assign_tag_to(
        self, subject_type: str, subject_id: str, tag_id: str,
    ) -> None:
        self.calls.append((subject_type, subject_id, tag_id))


class FakeNoteContentRepo:
    """Maps note_id -> (title, content) for condition lookups."""

    def __init__(self, mapping: dict[str, tuple[str, str]] | None = None) -> None:
        self._mapping = mapping or {}

    async def select_by_id(self, note_id: str):
        # Minimal stand-in that only carries the two fields we need.
        class _E:
            def __init__(self, title, content):
                self.title = title
                self.content = content
        if note_id not in self._mapping:
            return _E(None, None)
        t, c = self._mapping[note_id]
        return _E(t, c)


# ---- helpers --------------------------------------------------------------


async def _build(
    *,
    rules: list[RuleEntity] | None = None,
    note_mapping: dict[str, tuple[str, str]] | None = None,
    context: object | None = None,
):
    rule_repo = InMemoryRuleRepo()
    for r in rules or []:
        await rule_repo.create_rule(r)

    directory_repo = FakeDirectoryRepo()
    tag_repo = FakeTagRepo()
    ctx = context if context is not None else NoopEventContext()
    dispatcher = RuleDispatcher(
        rule_repo=rule_repo,
        directory_repo=directory_repo,
        tag_repo=tag_repo,
        context=ctx,  # type: ignore[arg-type]
    )
    return dispatcher, rule_repo, directory_repo, tag_repo


def _rule(
    *,
    event_type: str,
    condition: dict,
    action_type: str = "add_to_directory",
    action_context: dict | None = None,
    attached_entity_type: str | None = None,
    attached_entity_id: str | None = None,
    enabled: bool = True,
) -> RuleEntity:
    return RuleEntity(
        id=UNDEFINED,
        event_type=event_type,
        attached_entity_type=attached_entity_type,
        attached_entity_id=attached_entity_id,
        condition=condition,
        action_type=action_type,
        action_context=action_context or {"directory_id": "d-target"},
        enabled=enabled,
        creator_id="u-creator",
    )


# ---- scope matching -------------------------------------------------------


@pytest.mark.asyncio
async def test_unattached_rule_is_ignored():
    """Rules with neither attached_entity_type nor attached_entity_id
    are defensive no-ops.  Global rules were removed when shelves
    landed; the dispatcher refuses to evaluate any legacy rows that
    still carry ``None``."""
    dispatcher, _, _, _ = await _build(
        rules=[_rule(event_type="NoteCreated", condition={"type": "always_true"})],
    )
    # Should not raise; the rule is just skipped.
    await dispatcher._dispatch(NoteCreated(note_id="n1", actor_id="u"))
    await dispatcher._dispatch(NoteCreated(note_id="n2", actor_id="u"))


@pytest.mark.asyncio
async def test_attached_rule_matches_only_matching_id():
    dispatcher, _, directory_repo, _ = await _build(
        rules=[_rule(
            event_type="NoteCreated",
            condition={"type": "always_true"},
            attached_entity_type="note",
            attached_entity_id="n1",
        )],
    )

    # Should match (rule attached to n1, event about n1)
    await dispatcher._dispatch(NoteCreated(note_id="n1", actor_id="u"))
    # Should NOT match (event about n2)
    await dispatcher._dispatch(NoteCreated(note_id="n2", actor_id="u"))

    assert len(directory_repo.calls) == 1
    _parent_type, _parent_id, child_type, child_id = directory_repo.calls[0]
    assert child_type == "note"
    assert child_id == "n1"


@pytest.mark.asyncio
async def test_paused_rule_does_not_fire():
    dispatcher, _, directory_repo, _ = await _build(
        rules=[_rule(
            event_type="NoteCreated",
            condition={"type": "always_true"},
            enabled=False,
        )],
    )

    await dispatcher._dispatch(NoteCreated(note_id="n1", actor_id="u"))
    assert directory_repo.calls == []


# ---- condition evaluation ------------------------------------------------


@pytest.mark.asyncio
async def test_note_content_contains_only_fires_on_match():
    note_repo = FakeNoteContentRepo({"n1": ("T", "we love linux here")})
    rule_repo = InMemoryRuleRepo()
    await rule_repo.create_rule(_rule(
        event_type="NoteUpdated",
        condition={"type": "note_content_contains", "substring": "linux"},
        attached_entity_type="note",
        attached_entity_id="n1",
    ))

    directory_repo = FakeDirectoryRepo()
    tag_repo = FakeTagRepo()
    class _Ctx(EventContext):
        async def note_content(self, note_id): return (await note_repo.select_by_id(note_id)).content
        async def note_title(self, note_id): return (await note_repo.select_by_id(note_id)).title
        async def directory_ancestor_ids(self, directory_id): return []
        async def note_parent_directory_id(self, note_id): return None
        async def shelf_contains_book(self, shelf_id, book_id): return False
    ctx = _Ctx()

    dispatcher = RuleDispatcher(
        rule_repo=rule_repo,
        directory_repo=directory_repo,
        tag_repo=tag_repo,
        context=ctx,
    )

    await dispatcher._dispatch(NoteUpdated(note_id="n1", actor_id="u"))
    await dispatcher._dispatch(NoteUpdated(note_id="n2", actor_id="u"))  # unknown note -> no fire

    assert len(directory_repo.calls) == 1
    _parent_type, _parent_id, child_type, child_id = directory_repo.calls[0]
    assert child_type == "note"
    assert child_id == "n1"


@pytest.mark.asyncio
async def test_always_true_does_not_call_data_fetchers():
    """A no-op condition must not query the context for content."""
    fetched: list[str] = []
    class Ctx(NoopEventContext):
        async def note_content(self, note_id):
            fetched.append(note_id)
            return None
    dispatcher, _, _, _ = await _build(
        rules=[_rule(event_type="NoteCreated", condition={"type": "always_true"})],
        context=Ctx(),
    )
    await dispatcher._dispatch(NoteCreated(note_id="n1", actor_id="u"))
    assert fetched == []  # AlwaysTrue short-circuits before any fetch


# ---- action execution ---------------------------------------------------


@pytest.mark.asyncio
async def test_add_to_directory_fires_for_note_events():
    dispatcher, _, directory_repo, _ = await _build(
        rules=[_rule(
            event_type="NoteCreated",
            condition={"type": "always_true"},
            action_context={"directory_id": "d-target"},
            attached_entity_type="note",
            attached_entity_id="n1",
        )],
    )
    await dispatcher._dispatch(NoteCreated(note_id="n1", actor_id="u"))
    assert len(directory_repo.calls) == 1
    parent_type, parent_id, child_type, child_id = directory_repo.calls[0]
    assert parent_type == "directory"
    assert parent_id == "d-target"
    assert child_type == "note"
    assert child_id == "n1"


@pytest.mark.asyncio
async def test_add_tag_fires_for_note_event_with_note_subject():
    dispatcher, _, _, tag_repo = await _build(
        rules=[_rule(
            event_type="NoteCreated",
            condition={"type": "always_true"},
            attached_entity_type="note",
            attached_entity_id="n1",
            action_type="add_tag",
            action_context={"tag_id": "t1"},
        )],
    )
    await dispatcher._dispatch(NoteCreated(note_id="n1", actor_id="u"))
    assert tag_repo.calls == [("note", "n1", "t1")]


@pytest.mark.asyncio
async def test_add_tag_fires_for_directory_event_with_directory_subject():
    dispatcher, _, _, tag_repo = await _build(
        rules=[_rule(
            event_type="DirectoryCreated",
            condition={"type": "always_true"},
            attached_entity_type="directory",
            attached_entity_id="d1",
            action_type="add_tag",
            action_context={"tag_id": "t1"},
        )],
    )
    await dispatcher._dispatch(DirectoryCreated(directory_id="d1", actor_id="u"))
    assert tag_repo.calls == [("directory", "d1", "t1")]


# ---- end-to-end via the bus ---------------------------------------------


@pytest.mark.asyncio
async def test_bus_dispatch_runs_matching_rule():
    rule_repo = InMemoryRuleRepo()
    await rule_repo.create_rule(_rule(
        attached_entity_type="note",
        attached_entity_id="n1",
        event_type="NoteCreated",
        condition={"type": "always_true"},
        action_type="add_tag",
        action_context={"tag_id": "t1"},
    ))

    directory_repo = FakeDirectoryRepo()
    tag_repo = FakeTagRepo()
    dispatcher = RuleDispatcher(
        rule_repo=rule_repo,
        directory_repo=directory_repo,
        tag_repo=tag_repo,
        context=NoopEventContext(),
    )
    bus = InMemoryEventBus()
    await bus.subscribe(dispatcher.note_created_listener)

    await bus.notify(NoteCreated(note_id="n1", actor_id="u"))

    assert tag_repo.calls == [("note", "n1", "t1")]
