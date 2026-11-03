"""Storage contract for the permission audit trail.

The repo is intentionally thin: it persists already-authorized
grant/revoke rows.  Authorization and tuple normalization live in
:class:`src.services.permissions.PermissionServiceImpl`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Sequence

if TYPE_CHECKING:
    from src.db.entities.permission_audit import (
        FilterPermissionAudit,
        PermissionAuditEntity,
    )


class PermissionAuditRepoABC(ABC):
    """Persistence contract for ``public.permission_audit``.

    Implementations:
    * :class:`src.db.repos.permissions.postgres_audit.PostgresPermissionAuditRepo`
    """

    @abstractmethod
    async def record_many(
        self,
        entries: Sequence[PermissionAuditEntity],
    ) -> None:
        """Append ``entries`` to the trail.

        The caller has already authorized the mutation.  Implementations
        must not perform permission checks.
        """
        ...

    @abstractmethod
    async def get(
        self,
        filter: FilterPermissionAudit,
    ) -> List[PermissionAuditEntity]:
        """Return audit rows matching ``filter``, newest first.

        This read path is not used by the write-side service; it exists
        so a future audit UI can reuse the same interface.
        """
        ...


__all__ = ["PermissionAuditRepoABC"]