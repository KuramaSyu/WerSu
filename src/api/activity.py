"""Abstract base for ``activity`` storage.

The repo is intentionally thin: it manages persistence only.
Authorisation, view filtering, and gRPC mapping live elsewhere.

The ABC lives in :mod:`src.api` so the service code can depend on it
without importing the concrete Postgres implementation.  Concrete
implementations:

* :class:`src.db.repos.activity.postgres.PostgresActivityRepo`

The query DSL (filter dataclass + builder) is exported from this
module so callers can construct filters without reaching into the
entity file.  The strategy implementation (count / log_count) is an
implementation detail and is intentionally not re-exported here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from src.db.entities.activity import (
    ActivityEntity,
    ActivityFilterBuilder,
    ActivityScore,
    FilterActivity,
)


class ActivityRepoABC(ABC):
    """Storage contract for ``activity`` rows.

    Implementations must not perform any permission or business
    validation; they only translate requests into storage operations
    and surface the persisted entity back to the caller.

    Implementations:
    * :class:`src.db.repos.activity.postgres.PostgresActivityRepo`
    """

    @abstractmethod
    async def get_activities(
        self,
        filter: FilterActivity,
    ) -> List[ActivityEntity]:
        """Return activity history matching ``filter``.

        The filter must have ``filter.mode == "history"`` (the default
        shape produced by :meth:`ActivityFilterBuilder.use_history`).
        For aggregate ranking queries, see :meth:`get_most_used`.

        Args:
            filter: search filter.  Field semantics follow
                :class:`FilterActivity`:

                * :obj:`~src.api.undefined.UNDEFINED` -> column ignored.
                * :obj:`None` on nullable columns -> ``IS NULL``.
                * ``action_set`` -> ``action = ANY(...)``.
                * ``days`` -> ``at >= NOW() - INTERVAL 'N days'``;
                  ``None`` disables the window.
                * concrete values -> ``=`` for string / ENUM columns.

        Returns:
            List[ActivityEntity]: matching activity rows in reverse
            chronological order.

        Raises:
            ValueError: if ``filter.mode`` is not ``"history"``.
        """
        ...

    @abstractmethod
    async def get_most_used(
        self,
        filter: FilterActivity,
    ) -> List[ActivityScore]:
        """Return aggregate ranking matching ``filter``.

        The filter must have ``filter.mode == "most_used"``.  The
        repo picks the scoring algorithm from ``filter.algorithm``
        (defaulting to ``"count"``) and respects
        ``filter.unique_per_day`` when collapsing repeats.

        Args:
            filter: search filter, built with
                :meth:`ActivityFilterBuilder.show_most_used`.

        Returns:
            List[ActivityScore]: one row per note with the computed
            score.  Ordered by ``score`` descending; ties broken by
            ``note_id`` ascending for stable pagination.
        """
        ...

    @abstractmethod
    async def add_activity(self, activity: ActivityEntity) -> ActivityEntity:
        """Insert ``activity`` and return the persisted entity.

        The repository populates server-side defaults (notably
        ``id`` and ``at``) before returning.

        Args:
            activity: entity to insert.  ``id`` and ``at`` may be
                :obj:`~src.api.undefined.UNDEFINED`; ``action`` is
                required.  Exactly one of ``note_id`` /
                ``directory_id`` / ``role_id`` must be a concrete
                value, matching the ``action`` prefix
                (``note_*`` -> ``note_id``; ``directory_*`` ->
                ``directory_id``; ``role_*`` -> ``role_id``).

        Returns:
            ActivityEntity: the persisted entity with server-side
            defaults filled in.

        Raises:
            ValueError: if ``action`` is missing, if the target
                shape doesn't match ``action``, or if the action
                prefix is unknown.
        """
        ...

    @abstractmethod
    async def remove_activity_by_id(self, activity_id: str) -> None:
        """Delete the activity with the given id.

        Args:
            activity_id: id of the activity row to delete.

        Raises:
            ValueError: if no activity with ``activity_id`` exists, so
                callers can distinguish "already gone" from a real
                failure.
        """
        ...

    @abstractmethod
    async def edit_activity(self, activity: ActivityEntity) -> ActivityEntity:
        """Persist changes to an existing activity.

        The entity's ``id`` is required; every other field with a
        concrete value replaces the persisted column.
        :obj:`~src.api.undefined.UNDEFINED` fields are ignored;
        :obj:`None` explicitly clears the column.  ``id`` and ``at``
        are never overwritten via this path.

        Args:
            activity: entity carrying the new field values plus the
                ``id`` of the row to update.

        Returns:
            ActivityEntity: the persisted entity after the update.

        Raises:
            ValueError: if ``id`` is missing or no row matches it.
        """
        ...


__all__ = [
    "ActivityRepoABC",
    "ActivityFilterBuilder",
]