"""Application service contract for precomputed user statistics.

The service resolves the set of notes and directories a user can view
through :class:`~src.api.repos.permission_repo.PermissionRepoABC`
using a wildcard lookup (``note:UNDEFINED#view@user:<id>`` /
``directory:UNDEFINED#view@user:<id>``), asks the stats repo to
aggregate those ids, and persists the result.

Implementations:
* :class:`src.services.calculated_stats_service.CalculatedStatsServiceImpl`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.other.user_context import UserContextABC
from src.db.entities.calculated_stats import (
    CalculatedStatsEntity,
    CalculatedStatsFilter,
)


class CalculatedStatsServiceABC(ABC):
    """Read + recompute precomputed user statistics."""

    @abstractmethod
    async def get_stats(
        self,
        actor: UserContextABC,
        user_id: Optional[str] = None,
    ) -> Optional[CalculatedStatsEntity]:
        """Return the stored stats for ``user_id`` (default: the actor).

        A caller may always read their own stats.  Reading another
        user's stats requires page-administrator access
        (``page:global#admin``).

        Args:
            actor: caller identity.
            user_id: target user; ``None`` means ``actor.user_id``.

        Returns:
            The stored stats row, or ``None`` when no row exists yet.

        Raises:
            PermissionError: the actor is neither the target user nor a
                page administrator.
        """
        ...

    @abstractmethod
    async def recalculate(
        self,
        filter: Optional[CalculatedStatsFilter] = None,
    ) -> List[CalculatedStatsEntity]:
        """Recompute and persist stats for the users selected by ``filter``.

        ``filter=None`` or ``filter.user_ids=None`` recomputes every
        user.  Passing an explicit list restricts work to those users;
        an empty list performs no work.

        This method is intentionally callable without a
        :class:`UserContextABC` so the daily background process can
        run it.  Callers exposing it over the network must enforce
        their own authorisation (the gRPC adapter currently only
        exposes the read RPC).

        Args:
            filter: optional user-id filter.

        Returns:
            The freshly persisted stats rows, in processing order.
        """
        ...


__all__ = ["CalculatedStatsServiceABC"]
