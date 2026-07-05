"""SqlStatement - dialect-agnostic SQL plus its bound parameters.

The unit of work exchanged between an
:class:`src.db.sql_builders.SqlBuilderABC` and the executor it feeds
into.  ``sql`` is the dialect-specific statement (Postgres ``$1``,
SQLite ``?``, ...); ``params`` is a tuple of bound values in the
same order as the placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple


@dataclass(frozen=True)
class SqlStatement:
    """Prepared SQL statement with its bound parameters.

    Attributes:
        sql: dialect-specific SQL string.
        params: values bound to the placeholders, in order.
    """

    sql: str
    params: Tuple[object, ...] = ()

    def with_params(self, params: Sequence[object]) -> "SqlStatement":
        """Return a copy with ``params`` replaced."""
        return SqlStatement(self.sql, tuple(params))
