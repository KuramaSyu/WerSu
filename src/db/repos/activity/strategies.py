"""Most-used ranking strategies for the ``activity`` log.

A strategy builds the SELECT / GROUP BY / ORDER BY fragment used by
:class:`src.db.repos.activity.postgres.PostgresActivityRepo` when
serving ``most_used`` mode queries.  The base WHERE clause (filters,
time window, directory subtree expansion) is assembled by the repo;
this module only owns the projection + scoring + grouping shape.

Strategies are an implementation detail -- callers must not import
this module directly.  Use :class:`ActivityFilterBuilder` and let the
repo pick the right strategy from ``filter.algorithm``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Literal, Optional

from src.db.entities.activity import FilterActivity


@dataclass(frozen=True)
class StrategyResult:
    """The SQL fragment a strategy produces.

    Attributes:
        select_columns: comma-separated columns to project, including
            ``score`` (e.g. ``"note_id, COUNT(*) AS score"``).
        score_expr: SQL expression for the score column.  Mirrored on
            the result for callers that want to render it separately.
        group_by: comma-separated GROUP BY columns, or empty string.
        order_by: ORDER BY fragment (the repo appends ``LIMIT $N``
            after binding its own params).
    """

    select_columns: str
    score_expr: str
    group_by: str
    order_by: str


class MostUsedStrategyABC(ABC):
    """Strategy interface for ``most_used`` mode queries."""

    @abstractmethod
    def build(
        self,
        filter: FilterActivity,
        resolved_note_ids: Optional[List[str]],
        resolved_directory_ids: Optional[List[str]],
        dialect: str = "postgres",
    ) -> StrategyResult:
        """Build the SELECT/GROUP BY/ORDER BY fragment.

        Args:
            filter: the activity filter (for ``unique_per_day`` etc.).
            resolved_note_ids: note ids the directory subtree expansion
                produced, or ``None`` when no directory filter is set.
            resolved_directory_ids: directory ids the directory subtree
                expansion produced, or ``None`` when no directory
                filter is set.
            dialect: ``"postgres"`` or ``"sqlite"`` -- some aggregate
                forms (e.g. ``COUNT(DISTINCT (a, b))``) only work in
                Postgres; SQLite needs a concatenation workaround.

        Returns:
            StrategyResult: the assembled SQL fragment.
        """
        ...


def _score_expr(filter: FilterActivity, dialect: str) -> str:
    """Compute the SQL score expression for ``filter`` and ``dialect``.

    Postgres uses ``COUNT(DISTINCT (actor_id, DATE(at)))`` -- the
    tuple form inside ``DISTINCT`` is valid in Postgres.

    SQLite has no row-value syntax, so we collapse
    ``(actor_id, DATE(at))`` into a single concatenated key:
    ``COUNT(DISTINCT actor_id || '|' || DATE(at))``.  The separator
    is unlikely to appear in either value, so distinct keys stay
    distinct.
    """
    if filter.unique_per_day is True:
        if dialect == "postgres":
            return "COUNT(DISTINCT (actor_id, DATE(at)))"
        return "COUNT(DISTINCT actor_id || '|' || DATE(at))"
    return "COUNT(*)"


class CountStrategy(MostUsedStrategyABC):
    """Plain ``COUNT(*)`` ranking.

    The only behavioural knob is :attr:`FilterActivity.unique_per_day`,
    which switches the inner aggregation to
    ``COUNT(DISTINCT ...)`` so a single actor spamming events only
    counts once per day.
    """

    def build(
        self,
        filter: FilterActivity,
        resolved_note_ids: Optional[List[str]],
        resolved_directory_ids: Optional[List[str]],
        dialect: str = "postgres",
    ) -> StrategyResult:
        """Return ``note_id, COUNT(*) AS score`` grouped by ``note_id``."""
        score_expr = _score_expr(filter, dialect)
        return StrategyResult(
            select_columns=f"note_id, {score_expr} AS score",
            score_expr=score_expr,
            group_by="note_id",
            order_by="score DESC",
        )


class LogCountStrategy(MostUsedStrategyABC):
    """Log-flattened scoring: ``LN(COUNT(*) + 1)``.

    Reduces the dominance of super-popular notes in the ranking by
    taking the natural logarithm of the raw count.  ``+ 1`` keeps
    notes with a single event at a non-zero score.
    """

    def build(
        self,
        filter: FilterActivity,
        resolved_note_ids: Optional[List[str]],
        resolved_directory_ids: Optional[List[str]],
        dialect: str = "postgres",
    ) -> StrategyResult:
        """Return ``note_id, LN(<score> + 1) AS score`` grouped by ``note_id``."""
        score_expr = f"LN({_score_expr(filter, dialect)} + 1)"
        return StrategyResult(
            select_columns=f"note_id, {score_expr} AS score",
            score_expr=score_expr,
            group_by="note_id",
            order_by="score DESC",
        )


_STRATEGIES: dict[str, type[MostUsedStrategyABC]] = {
    "count": CountStrategy,
    "log_count": LogCountStrategy,
}


def get_strategy(algorithm: Literal["count", "log_count"]) -> MostUsedStrategyABC:
    """Return the strategy instance for ``algorithm``.

    Args:
        algorithm: the strategy name.  Validated here so the repo
            raises a single, well-typed error for unknown values.

    Raises:
        ValueError: if ``algorithm`` is not one of the registered
            strategy names.
    """
    try:
        cls = _STRATEGIES[algorithm]
    except KeyError as exc:
        raise ValueError(
            f"unknown most_used algorithm {algorithm!r}; "
            f"valid: {sorted(_STRATEGIES)}"
        ) from exc
    return cls()


__all__ = [
    "MostUsedStrategyABC",
    "StrategyResult",
    "CountStrategy",
    "LogCountStrategy",
    "get_strategy",
]