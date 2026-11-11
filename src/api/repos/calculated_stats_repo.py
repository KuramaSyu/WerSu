"""Storage contract for precomputed ``calculated_stats`` rows.

The repo owns both the stats table and the two small aggregate
queries used to fill it.  Permission resolution happens in the
service layer; the repo only receives concrete note / directory ids
and returns counts.

Implementations:
* :class:`src.db.repos.calculated_stats.postgres.PostgresCalculatedStatsRepo`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from src.db.entities.calculated_stats import (
        CalculatedStatsEntity,
        DirectoryAggregate,
        NoteAggregate,
    )


class CalculatedStatsRepoABC(ABC):
    """Persistence + aggregation contract for calculated user stats."""

    @abstractmethod
    async def list_user_ids(self) -> List[str]:
        """Return every **human** user id that can own a stats row.

        System and temporary users are excluded: the wildcard
        ``view`` lookup is meaningless for them.  Used by
        :meth:`CalculatedStatsServiceABC.recalculate` when the caller
        does not pass an explicit user filter.
        """
        ...

    @abstractmethod
    async def get(self, user_id: str) -> Optional[CalculatedStatsEntity]:
        """Return the stored stats for ``user_id``; ``None`` when absent."""
        ...

    @abstractmethod
    async def fetch_by_user_ids(
        self, user_ids: List[str]
    ) -> List[CalculatedStatsEntity]:
        """Return every stored stats row for ``user_ids``.

        Unknown ids are skipped.  An empty input returns ``[]``
        without issuing a query.
        """
        ...

    @abstractmethod
    async def upsert(
        self, stats: CalculatedStatsEntity
    ) -> CalculatedStatsEntity:
        """Insert or replace the row keyed by ``stats.user_id``."""
        ...

    @abstractmethod
    async def aggregate_notes(self, note_ids: List[str]) -> NoteAggregate:
        """Count notes and sum their content length for ``note_ids``.

        An empty input returns a zero aggregate.
        """
        ...

    @abstractmethod
    async def aggregate_directories(
        self, directory_ids: List[str]
    ) -> DirectoryAggregate:
        """Split ``directory_ids`` into upper (books) and nested (chapters).

        A directory counts as a *book* when it has no parent row in
        ``note.directory_subdirectory`` and as a *chapter* when it has
        at least one parent row.  An empty input returns a zero
        aggregate.
        """
        ...


__all__ = ["CalculatedStatsRepoABC"]
