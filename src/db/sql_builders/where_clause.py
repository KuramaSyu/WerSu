"""WhereClause - composable WHERE clause with an AND group and an OR group.

Two parallel groups of ``(column, value)`` pairs joined separately
and combined with ``AND``; the OR group is parenthesised so e.g.::

    WhereClause.build(and={"status": "active"}, or={"region": "EU", "country": "DE"})

emits ``WHERE (status = $1) AND (region = $2 OR country = $3)``.

Constrained to one AND group + one OR group; anything more exotic
should fall through to :meth:`SqlBuilderABC.fetch`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple


@dataclass
class WhereClause:
    """AND/OR WHERE clause.

    Attributes:
        and_pairs: ``(column, value)`` pairs joined by ``AND``.
        or_pairs: ``(column, value)`` pairs joined by ``OR``.

    Note:
        Both groups are tuples (not dicts) so the same column may
        legitimately appear with two different values.  The
        :meth:`build` factory accepts a dict for convenience; that
        collapses duplicate keys inside one group.  Construct the
        class directly if you need repeats.
    """

    and_pairs: Tuple[Tuple[str, object], ...] = ()
    or_pairs: Tuple[Tuple[str, object], ...] = ()

    @classmethod
    def build(
        cls,
        *,
        and_: dict | None = None,
        or_: dict | None = None,
    ) -> "WhereClause":
        """Build a clause from two optional dicts.

        Args:
            and_: pairs joined by ``AND``.  ``None`` => no AND group.
            or_: pairs joined by ``OR``.  ``None`` => no OR group.
        """
        return cls(
            and_pairs=tuple((and_ or {}).items()),
            or_pairs=tuple((or_ or {}).items()),
        )

    @property
    def is_empty(self) -> bool:
        """``True`` when both groups are empty."""
        return not self.and_pairs and not self.or_pairs

    def total_params(self) -> int:
        """Number of bound values this clause consumes."""
        return len(self.and_pairs) + len(self.or_pairs)

    def all_pairs(self) -> Iterable[Tuple[str, object]]:
        """Yield ``(column, value)`` pairs in AND-then-OR order."""
        yield from self.and_pairs
        yield from self.or_pairs
