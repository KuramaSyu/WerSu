"""Domain entity and aggregates for the ``calculated_stats`` table.

The table stores one precomputed stats row per user.  The row is
refreshed by :class:`~src.services.calculated_stats_service.CalculatedStatsServiceImpl`
(usually from the daily background process) and read by the gRPC
:class:`~src.grpc_mod.calculated_stats_service.GrpcCalculatedStatsService`.

The aggregate dataclasses (:class:`NoteAggregate`,
:class:`DirectoryAggregate`) are the return shape of the repo's
counting helpers; they deliberately live next to the entity so the
service layer has one import for "stats-shaped" values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from src.api.other.undefined import UNDEFINED, UndefinedOr


@dataclass
class CalculatedStatsEntity:
    """One row of ``calculated_stats``.

    Attributes:
        user_id: owner of the stats row.  Primary key.
        total_chars: sum of ``length(content)`` over every note the
            user can view.
        total_notes: number of notes the user can view.
        total_chapters: number of visible directories that have at
            least one parent (nested / non-upper directories).
        total_books: number of visible directories without a parent
            (top-level / upper directories).
        calculated_at: when the row was last recomputed.
    """

    user_id: UndefinedOr[str] = UNDEFINED
    total_chars: UndefinedOr[int] = UNDEFINED
    total_notes: UndefinedOr[int] = UNDEFINED
    total_chapters: UndefinedOr[int] = UNDEFINED
    total_books: UndefinedOr[int] = UNDEFINED
    calculated_at: UndefinedOr[datetime] = UNDEFINED


@dataclass(frozen=True)
class NoteAggregate:
    """Aggregate result over a set of accessible notes."""

    total_notes: int = 0
    total_chars: int = 0


@dataclass(frozen=True)
class DirectoryAggregate:
    """Aggregate result over a set of accessible directories.

    ``books`` are directories with no parent row (upper directories);
    ``chapters`` are directories with at least one parent row
    (non-upper / nested directories).
    """

    total_books: int = 0
    total_chapters: int = 0


@dataclass(frozen=True)
class CalculatedStatsFilter:
    """Filter for :meth:`CalculatedStatsServiceABC.recalculate`.

    ``user_ids=None`` (the default) means "recalculate every user".
    An explicit list restricts the work to those users; an empty list
    is treated as "no users" and performs no writes.
    """

    user_ids: Optional[List[str]] = None


__all__ = [
    "CalculatedStatsEntity",
    "CalculatedStatsFilter",
    "DirectoryAggregate",
    "NoteAggregate",
]
