"""Fake permission audit repo for unit tests.

Records every entry in memory and can be armed to raise, so tests can
assert that an audit failure never fails the permission mutation.
"""

from __future__ import annotations

from typing import List, Sequence

from src.api.repos.permission_audit_repo import PermissionAuditRepoABC
from src.db.entities.permission_audit import (
    FilterPermissionAudit,
    PermissionAuditEntity,
)


class FakePermissionAuditRepo(PermissionAuditRepoABC):
    """In-memory spy/fake for :class:`PermissionAuditRepoABC`."""

    def __init__(self, *, fail: bool = False) -> None:
        self.entries: List[PermissionAuditEntity] = []
        self.record_calls: List[List[PermissionAuditEntity]] = []
        self.fail = fail

    async def record_many(
        self,
        entries: Sequence[PermissionAuditEntity],
    ) -> None:
        batch = list(entries)
        self.record_calls.append(batch)
        if self.fail:
            raise RuntimeError("audit store unavailable")
        self.entries.extend(batch)

    async def get(
        self,
        filter: FilterPermissionAudit,
    ) -> List[PermissionAuditEntity]:
        return list(self.entries)


__all__ = ["FakePermissionAuditRepo"]