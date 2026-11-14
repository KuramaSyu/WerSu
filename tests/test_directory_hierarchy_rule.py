"""Unit tests for :mod:`src.domain.rules.hierarchy`."""

from __future__ import annotations

import pytest

from src.domain.rules import (
    DIRECTORY_HIERARCHY_RULE_ENGINE,
    DirectoryHierarchyCycleError,
    DirectoryHierarchyState,
    DirectoryMustNotContainCycle,
)


def _enforce(child_id: str, parent_ids: tuple[str, ...], descendant_ids: tuple[str, ...] = ()) -> None:
    DIRECTORY_HIERARCHY_RULE_ENGINE.enforce(
        DirectoryHierarchyState(
            child_id=child_id,
            parent_ids=parent_ids,
            descendant_ids=descendant_ids,
        )
    )


def test_cycle_rule_allows_dropping_every_parent() -> None:
    _enforce("T1", ())


def test_cycle_rule_allows_unrelated_parent() -> None:
    _enforce("T1", ("T2",), descendant_ids=("A", "B"))


def test_cycle_rule_rejects_self_parent() -> None:
    with pytest.raises(DirectoryHierarchyCycleError) as exc_info:
        _enforce("T1", ("T1",))
    assert "T1" in str(exc_info.value)


def test_cycle_rule_rejects_parent_that_is_a_descendant() -> None:
    # T2 is a descendant of T1 -> adding T2 as T1's parent closes a loop.
    with pytest.raises(DirectoryHierarchyCycleError):
        _enforce("T1", ("T2",), descendant_ids=("T2", "X"))


def test_cycle_rule_is_pure() -> None:
    rule = DirectoryMustNotContainCycle()
    state = DirectoryHierarchyState(child_id="T1", parent_ids=("T2",))
    assert rule.check(state) is None
    assert rule.check(state) is None