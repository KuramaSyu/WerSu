"""Abstract read-side service for the ``activity`` log.

The :class:`ActivityStatisticsServiceABC` is the ergonomic facade over
:class:`~src.api.activity.ActivityRepoABC` used by callers that need
"history of X" or "most-viewed notes" without constructing an
:class:`~src.db.entities.activity.ActivityFilterBuilder` themselves.

All kwargs default to ``None`` and combine freely: passing both
``note_id`` and ``directory_id`` constrains to rows that match either
(n-1 in the dir subtree AND in the explicit note list).  Passing
neither resolves to "everything the actor can view": the service
asks :class:`~src.api.directory_repo.DirectoryRepo` for the actor's
visible top-level directories and passes each into the filter as a
subtree root.

Every method requires ``actor`` (first positional) so the service can
enforce view permissions on the requested targets and resolve "all"
without the caller having to thread permissions through itself.

Implementations:
* :class:`src.services.activity_statistics_service.PostgresActivityStatisticsService`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Literal, Optional, Sequence

from src.api.user_context import UserContextABC
from src.db.entities.activity import ActivityEntity, ActivityScore


Algorithm = Literal["count", "log_count"]


class ActivityStatisticsServiceABC(ABC):
    """Read-side facade over the activity log.

    Implementations:
    * :class:`~src.services.activity_statistics_service.DefaultActivityStatisticsService`
    """

    @abstractmethod
    async def get_history(
        self,
        actor: UserContextABC,
        *,
        note_id: Optional[str] = None,
        directory_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        actions: Optional[Sequence[str]] = None,
        role_id: Optional[str] = None,
        accessed_as: Optional[str] = None,
        days: Optional[int] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[ActivityEntity]:
        """Return activity history rows the ``actor`` is allowed to see.

        When neither note nor directory id is set,
        the service resolves every directory the actor can view and
        expands each into its subtree.

        Args:
            actor: caller identity; required and always first
                positional.
            note_id: restrict to this note.
            directory_id: restrict to this directory subtree.
            actor_id: filter rows where ``actor_id == actor_id``.
            actions: restrict to any of these action kinds.
            role_id: filter rows where ``role_id == role_id``.
            accessed_as: filter rows by ``accessed_as`` ("user" /
                "system").
            days: only events in the last ``days`` days.
            limit: cap on returned rows.
            offset: skip the first ``offset`` rows.

        Returns:
            List[ActivityEntity]: matching rows in reverse
            chronological order.

        Raises:
            PermissionError: ``actor`` cannot view the requested
                ``note_id`` or ``directory_id``.
        """

    @abstractmethod
    async def get_most_used(
        self,
        actor: UserContextABC,
        *,
        note_id: Optional[str] = None,
        directory_id: Optional[str] = None,
        actions: Optional[Sequence[str]] = None,
        role_id: Optional[str] = None,
        accessed_as: Optional[str] = None,
        days: Optional[int] = None,
        algorithm: Algorithm = "count",
        unique_per_day: bool = False,
        limit: Optional[int] = None,
    ) -> List[ActivityScore]:
        """Return aggregate note ranking the ``actor`` is allowed to see.

        Same permission semantics as :meth:`get_history`.  The
        ``algorithm`` knob picks the scoring function:

        * ``"count"`` -- raw event count per note.
        * ``"log_count"`` -- ``ln(count + 1)``; reduces dominance of
          viral notes.

        ``unique_per_day`` is an orthogonal pre-aggregation filter
        that collapses repeats to one count per (actor, day) pair
        before the chosen algorithm runs.  It composes with any
        algorithm.

        Args:
            actor: caller identity; required and always first
                positional.
            note_id: restrict to this note.
            directory_id: restrict to this directory subtree.
            actions: restrict to any of these action kinds.
            role_id: filter rows where ``role_id == role_id``.
            accessed_as: filter rows by ``accessed_as``.
            days: only events in the last ``days`` days.
            algorithm: scoring function to use.
            unique_per_day: collapse repeats to one count per
                (actor, day) pair before scoring.
            limit: cap on returned rows.

        Returns:
            List[ActivityScore]: one row per note with the computed
            score.  Ordered by ``score`` descending.

        Raises:
            PermissionError: ``actor`` cannot view the requested
                ``note_id`` or ``directory_id``.
        """


__all__ = [
    "ActivityStatisticsServiceABC",
    "Algorithm",
]