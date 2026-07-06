"""Dialect-aware fluent SQL builders.

Canonical usage guide lives on
:class:`src.db.sql_builders.SqlBuilderABC` -- that's the only doc
to read on first contact.

Contents at a glance:

* :class:`SqlStatement` -- (sql, params) tuple.
* :class:`SqlBuilderABC` + :class:`PostgresSqlBuilder`,
  :class:`SqliteSqlBuilder` -- abstract entry + dialect flavours.
* :class:`InsertStmtABC` / :class:`UpdateStmtABC` /
  :class:`DeleteStmtABC` / :class:`SelectStmtABC` -- the staged
  builders returned by each fluent entry method.
* :class:`SelectFromStmtABC` -- the staged builder returned by
  :meth:`SqlBuilderABC.select_from` for SELECTs whose WHERE
  clause carries pair shapes the dict-based
  :meth:`SelectStmtABC.where` can't express (``IS NULL``, raw
  ``>= $N`` fragments, ``IN ($N, $N+1)`` lists, OR groups).
* :class:`WhereClause` -- the AND/OR groups used by every
  ``.where()`` method.
* :class:`SqlBuilderFactory` -- pick a builder by dialect name.
"""

from src.db.sql_builders.sql_builder import (
    PostgresSqlBuilder,
    SqlBuilderABC,
    SqlBuilderFactory,
    SqliteSqlBuilder,
)
from src.db.sql_builders.sql_statement import SqlStatement
from src.db.sql_builders.statements import (
    DeleteStmtABC,
    InsertStmtABC,
    PostgresSelectFromStmt,
    SelectFromStmtABC,
    SelectStmtABC,
    SqliteSelectFromStmt,
    UpdateStmtABC,
)
from src.db.sql_builders.where_clause import (
    WhereClause,
    WherePair,
    is_is_null_pair,
    is_raw_pair,
    render_where,
)

__all__ = [
    "DeleteStmtABC",
    "InsertStmtABC",
    "PostgresSelectFromStmt",
    "PostgresSqlBuilder",
    "SelectFromStmtABC",
    "SelectStmtABC",
    "SqlBuilderABC",
    "SqlBuilderFactory",
    "SqlStatement",
    "SqliteSelectFromStmt",
    "SqliteSqlBuilder",
    "UpdateStmtABC",
    "WhereClause",
    "WherePair",
    "is_is_null_pair",
    "is_raw_pair",
    "render_where",
]