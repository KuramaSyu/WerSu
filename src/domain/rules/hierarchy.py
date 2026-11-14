"""Directory hierarchy rules.

The directory tree is a DAG: a directory may have many parents and
many children, but a parent edge must never close a loop.  A cycle
makes permission inheritance circular and turns the recursive delete
cascade in :meth:`~src.services.directory.DirectoryServiceImpl.delete_directory`
into an infinite recursion, so the invariant is enforced before any
store is touched.

The check reuses the hierarchy helper that already owns the graph walk
(:meth:`~src.api.repos.directory_repo.DirectoryHelperMixin.get_children_of`):
the caller collects the descendants of the child being re-parented and
the rule only inspects the sets.  ``child -> ... -> parent`` closes a
loop exactly when one of the proposed parents is one of those
descendants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from src.domain.rules.base import RuleABC, RuleEngine, RuleViolation


MAX_HIERARCHY_DEPTH = 10_000
"""Depth bound for a "walk the whole subtree" hierarchy query.

The repo BFS keeps a visited set, so it terminates even on a cyclic
graph; the bound only has to exceed any real tree depth.  Callers pass
this when they need the full descendant set rather than a shallow read.
"""


@dataclass(frozen=True)
class DirectoryHierarchyState:
    """The parent edges a mutation *would* add.

    Attributes:
        child_id: directory whose parent set is being rewritten.
        parent_ids: the parents the child would end up with.
        descendant_ids: every directory reachable by walking ``parent``
            edges **down** from ``child_id`` (i.e. its subtree).  A
            cycle is closed exactly when a proposed parent appears in
            this set.
    """

    child_id: str
    parent_ids: Tuple[str, ...] = ()
    descendant_ids: Tuple[str, ...] = ()


class DirectoryHierarchyCycleError(RuleViolation):
    """Raised when a parent edge would close a loop in the directory DAG."""

    def __init__(self, child_id: str, parent_ids: Tuple[str, ...] = ()) -> None:
        parents = ", ".join(repr(p) for p in parent_ids) or "itself"
        super().__init__(
            f"directory {child_id!r} cannot be parented by {parents}: "
            "that would create a directory cycle"
        )
        self.child_id = child_id
        self.parent_ids = parent_ids


class DirectoryMustNotContainCycle(RuleABC[DirectoryHierarchyState]):
    """Reject a parent set that would make a directory its own descendant."""

    def check(self, context: DirectoryHierarchyState) -> None:
        """Raise when the proposed parents would close a cycle."""
        parents = tuple(str(p) for p in context.parent_ids)
        child = str(context.child_id)
        if child in parents:
            raise DirectoryHierarchyCycleError(child, parents)
        descendants = {str(d) for d in context.descendant_ids}
        if descendants.intersection(parents):
            raise DirectoryHierarchyCycleError(child, parents)


#: Default rule set for directory re-parenting.
DIRECTORY_HIERARCHY_RULE_ENGINE: RuleEngine[DirectoryHierarchyState] = RuleEngine(
    (DirectoryMustNotContainCycle(),)
)


__all__ = [
    "DIRECTORY_HIERARCHY_RULE_ENGINE",
    "DirectoryHierarchyCycleError",
    "DirectoryHierarchyState",
    "DirectoryMustNotContainCycle",
    "MAX_HIERARCHY_DEPTH",
]