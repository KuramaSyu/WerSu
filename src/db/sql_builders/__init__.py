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
    SelectStmtABC,
    UpdateStmtABC,
)
from src.db.sql_builders.where_clause import WhereClause

__all__ = [
    "DeleteStmtABC",
    "InsertStmtABC",
    "PostgresSqlBuilder",
    "SelectStmtABC",
    "SqlBuilderABC",
    "SqlBuilderFactory",
    "SqlStatement",
    "SqliteSqlBuilder",
    "UpdateStmtABC",
    "WhereClause",
]
