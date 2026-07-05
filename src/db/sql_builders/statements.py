"""Staged SQL statement builders.

Each statement kind (insert, update, delete, select) has an ABC and
two concrete subclasses -- one for Postgres, one for SQLite.  The
fluent methods live on the abstract base; the placeholder style
(``$1`` vs ``?``) and any dialect-specific syntax quirks live on the
subclasses.

For the fluent chain shapes and worked examples see
:class:`src.db.sql_builders.sql_builder.SqlBuilderABC`, which is the
canonical usage guide for this package.

Each staged builder mutates internal state then emits a
:class:`SqlStatement` from ``build()``.  No I/O, no connection,
just text.  This makes the builders trivially unit-testable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.db.sql_builders.sql_statement import SqlStatement
from src.db.sql_builders.where_clause import WhereClause


class _PlaceholderStyleMixin:
    """Mixin that knows how to number placeholders for one dialect.

    Each dialect builder inherits this mixin with the right
    ``style`` set.  Postgres uses ``$1``, SQLite uses ``?``
    repeatedly; both keep a running counter so compound clauses
    (WHERE + RETURNING, etc.) keep their placeholder indices
    consistent.
    """

    style: str = "?"  # set per dialect

    def ph(self, n: int) -> str:
        """Placeholder for the n-th parameter (1-indexed).

        Postgres uses ``$n``; SQLite uses ``?`` for every slot.
        The numbering is kept contiguous across the whole statement
        so the param list binds in the order they appear.
        """
        return f"${n}" if self.style == "$" else "?"


class InsertStmtABC(ABC):
    """Fluent ``INSERT`` staged builder.

    Per-dialect subclasses (:class:`PostgresInsertStmt`,
    :class:`SqliteInsertStmt`) own the placeholder style and
    ``ON CONFLICT`` syntax; this ABC owns the chain shape.
    """

    @abstractmethod
    def into(self, table: str) -> "InsertStmtABC":
        """Bind the target table."""

    @abstractmethod
    def columns(self, *cols: str) -> "InsertStmtABC":
        """Pin the columns explicitly.  :meth:`values` with kwargs
        infers them for you, so this is only used when the column
        list comes from outside (e.g. :class:`src.db.table.Table`).
        """

    @abstractmethod
    def values(self, *positional: Any, **kwargs: Any) -> "InsertStmtABC":
        """Bind the values.  Positional and keyword forms are
        mutually exclusive -- mixing them raises :exc:`TypeError`.
        """

    @abstractmethod
    def on_conflict(self, fragment: str) -> "InsertStmtABC":
        """Append a raw ``ON CONFLICT <fragment>`` clause.

        Args:
            fragment: dialect-specific conflict handler, e.g.
                ``"(id) DO NOTHING"``.
        """

    @abstractmethod
    def returning(self, *cols: str) -> "InsertStmtABC":
        """Append ``RETURNING <cols>``.  No args => ``RETURNING *``."""

    @abstractmethod
    def build(self) -> SqlStatement:
        """Materialise the staged state into a :class:`SqlStatement`."""


class UpdateStmtABC(ABC):
    """Fluent ``UPDATE`` staged builder."""

    @abstractmethod
    def table(self, name: str) -> "UpdateStmtABC":
        """Bind the target table."""

    @abstractmethod
    def set(self, **kwargs: Any) -> "UpdateStmtABC":
        """Bind the SET columns.  Each kwarg becomes ``column = placeholder``."""

    @abstractmethod
    def where(
        self,
        and_: Optional[Dict[str, Any]] = None,
        or_: Optional[Dict[str, Any]] = None,
    ) -> "UpdateStmtABC":
        """Attach a WHERE clause.

        Args:
            and_: pairs joined by ``AND``.  Trailing-underscore
                form is required because ``and`` is reserved.
            or_: pairs joined by ``OR`` (then parenthesised).
        """

    @abstractmethod
    def returning(self, *cols: str) -> "UpdateStmtABC":
        """Append ``RETURNING <cols>``."""

    @abstractmethod
    def build(self) -> SqlStatement:
        """Materialise the staged state into a :class:`SqlStatement`."""


class DeleteStmtABC(ABC):
    """Fluent ``DELETE`` staged builder."""

    @abstractmethod
    def from_table(self, name: str) -> "DeleteStmtABC":
        """Bind the target table."""

    @abstractmethod
    def where(
        self,
        and_: Optional[Dict[str, Any]] = None,
        or_: Optional[Dict[str, Any]] = None,
    ) -> "DeleteStmtABC":
        """Attach a WHERE clause; see :meth:`UpdateStmtABC.where`."""

    @abstractmethod
    def returning(self, *cols: str) -> "DeleteStmtABC":
        """Append ``RETURNING <cols>``."""

    @abstractmethod
    def build(self) -> SqlStatement:
        """Materialise the staged state into a :class:`SqlStatement`."""


class SelectStmtABC(ABC):
    """Fluent ``SELECT`` staged builder."""

    @abstractmethod
    def columns(self, *cols: str) -> "SelectStmtABC":
        """Pin the columns; defaults to ``*`` on :meth:`build`."""

    @abstractmethod
    def from_table(self, name: str) -> "SelectStmtABC":
        """Bind the target table."""

    @abstractmethod
    def where(
        self,
        and_: Optional[Dict[str, Any]] = None,
        or_: Optional[Dict[str, Any]] = None,
    ) -> "SelectStmtABC":
        """Attach a WHERE clause; see :meth:`UpdateStmtABC.where`."""

    @abstractmethod
    def order_by(self, fragment: str) -> "SelectStmtABC":
        """Append a raw ``ORDER BY`` fragment."""

    @abstractmethod
    def limit(self, n: int) -> "SelectStmtABC":
        """Append ``LIMIT n``."""

    @abstractmethod
    def offset(self, n: int) -> "SelectStmtABC":
        """Append ``OFFSET n``."""

    @abstractmethod
    def build(self) -> SqlStatement:
        """Materialise the staged state into a :class:`SqlStatement`."""


class _PostgresCommon:
    """Shared formatting helpers for Postgres staged builders."""

    style = "$"

    @staticmethod
    def _returning_clause(*cols: str) -> str:
        return "RETURNING " + (", ".join(cols) if cols else "*")

    @staticmethod
    def _where_sql(where: WhereClause, start_n: int) -> Tuple[str, Tuple[Any, ...]]:
        """Render ``where`` to a ``$n``-numbered SQL fragment.

        Returns ``(sql_fragment, bound_params)``.
        """
        if where.is_empty:
            return "", ()

        parts: List[str] = []
        params: List[Any] = []
        n = start_n

        if where.and_pairs:
            sub = " AND ".join(f"{col} = ${n + i}" for i, (col, _) in enumerate(where.and_pairs))
            parts.append(f"({sub})")
            params.extend(v for _, v in where.and_pairs)
            n += len(where.and_pairs)

        if where.or_pairs:
            sub = " OR ".join(f"{col} = ${n + i}" for i, (col, _) in enumerate(where.or_pairs))
            parts.append(f"({sub})")
            params.extend(v for _, v in where.or_pairs)

        return " AND ".join(parts), tuple(params)


class PostgresInsertStmt(_PostgresCommon, InsertStmtABC):
    """Postgres ``INSERT`` staged builder."""

    def __init__(self) -> None:
        self._table: Optional[str] = None
        self._columns: List[str] = []
        self._values: List[Any] = []
        self._on_conflict = ""
        self._returning: Optional[Tuple[str, ...]] = None

    def into(self, table: str) -> "PostgresInsertStmt":
        self._table = table
        return self

    def columns(self, *cols: str) -> "PostgresInsertStmt":
        self._columns = list(cols)
        return self

    def values(self, *positional: Any, **kwargs: Any) -> "PostgresInsertStmt":
        if positional and kwargs:
            raise TypeError("values() takes positional or keyword args, not both")
        if kwargs:
            self._columns = list(kwargs.keys())
            self._values = list(kwargs.values())
        else:
            self._values = list(positional)
        return self

    def on_conflict(self, fragment: str) -> "PostgresInsertStmt":
        self._on_conflict = fragment
        return self

    def returning(self, *cols: str) -> "PostgresInsertStmt":
        self._returning = tuple(cols) if cols else ("*",)
        return self

    def build(self) -> SqlStatement:
        if self._table is None:
            raise ValueError("insert().into(<table>) is required before build()")
        if not self._columns:
            raise ValueError("insert() needs either values(...) or columns(...)")

        placeholders = [f"${i + 1}" for i in range(len(self._values))]
        parts = [
            f"INSERT INTO {self._table} ({', '.join(self._columns)})",
            f"VALUES ({', '.join(placeholders)})",
        ]
        if self._on_conflict:
            parts.append(f"ON CONFLICT {self._on_conflict}")
        if self._returning:
            parts.append(self._returning_clause(*self._returning))

        return SqlStatement("\n".join(parts) + "\n", tuple(self._values))


class PostgresUpdateStmt(_PostgresCommon, UpdateStmtABC):
    """Postgres ``UPDATE`` staged builder."""

    def __init__(self) -> None:
        self._table: Optional[str] = None
        self._set: List[Tuple[str, Any]] = []
        self._where = WhereClause()
        self._returning: Optional[Tuple[str, ...]] = None

    def table(self, name: str) -> "PostgresUpdateStmt":
        self._table = name
        return self

    def set(self, **kwargs: Any) -> "PostgresUpdateStmt":
        self._set = list(kwargs.items())
        return self

    def where(
        self,
        and_: Optional[Dict[str, Any]] = None,
        or_: Optional[Dict[str, Any]] = None,
    ) -> "PostgresUpdateStmt":
        self._where = WhereClause.build(and_=and_, or_=or_)
        return self

    def returning(self, *cols: str) -> "PostgresUpdateStmt":
        self._returning = tuple(cols) if cols else ("*",)
        return self

    def build(self) -> SqlStatement:
        if self._table is None:
            raise ValueError("update().table(<name>) is required before build()")
        if not self._set:
            raise ValueError("update() needs at least one column in set(...)")

        set_placeholders = [f"${i + 1}" for i in range(len(self._set))]
        set_sql = ", ".join(f"{col} = {ph}" for (col, _), ph in zip(self._set, set_placeholders))

        start_n = len(self._set) + 1
        where_sql, where_params = self._where_sql(self._where, start_n=start_n)

        parts = [
            f"UPDATE {self._table}",
            f"SET {set_sql}",
        ]
        if where_sql:
            parts.append(f"WHERE {where_sql}")
        if self._returning:
            parts.append(self._returning_clause(*self._returning))

        set_values = [v for _, v in self._set]
        return SqlStatement("\n".join(parts) + "\n", tuple(set_values) + where_params)


class PostgresDeleteStmt(_PostgresCommon, DeleteStmtABC):
    """Postgres ``DELETE`` staged builder."""

    def __init__(self) -> None:
        self._table: Optional[str] = None
        self._where = WhereClause()
        self._returning: Optional[Tuple[str, ...]] = None

    def from_table(self, name: str) -> "PostgresDeleteStmt":
        self._table = name
        return self

    def where(
        self,
        and_: Optional[Dict[str, Any]] = None,
        or_: Optional[Dict[str, Any]] = None,
    ) -> "PostgresDeleteStmt":
        self._where = WhereClause.build(and_=and_, or_=or_)
        return self

    def returning(self, *cols: str) -> "PostgresDeleteStmt":
        self._returning = tuple(cols) if cols else ("*",)
        return self

    def build(self) -> SqlStatement:
        if self._table is None:
            raise ValueError(
                "delete().from_table(<name>) is required before build()"
            )

        where_sql, where_params = self._where_sql(self._where, start_n=1)

        parts = [f"DELETE FROM {self._table}"]
        if where_sql:
            parts.append(f"WHERE {where_sql}")
        if self._returning:
            parts.append(self._returning_clause(*self._returning))

        return SqlStatement("\n".join(parts) + "\n", where_params)


class PostgresSelectStmt(_PostgresCommon, SelectStmtABC):
    """Postgres ``SELECT`` staged builder."""

    def __init__(self) -> None:
        self._columns: Tuple[str, ...] = ()
        self._table: Optional[str] = None
        self._where = WhereClause()
        self._order_by: Optional[str] = None
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None

    def columns(self, *cols: str) -> "PostgresSelectStmt":
        self._columns = cols
        return self

    def from_table(self, name: str) -> "PostgresSelectStmt":
        self._table = name
        return self

    def where(
        self,
        and_: Optional[Dict[str, Any]] = None,
        or_: Optional[Dict[str, Any]] = None,
    ) -> "PostgresSelectStmt":
        self._where = WhereClause.build(and_=and_, or_=or_)
        return self

    def order_by(self, fragment: str) -> "PostgresSelectStmt":
        self._order_by = fragment
        return self

    def limit(self, n: int) -> "PostgresSelectStmt":
        self._limit = n
        return self

    def offset(self, n: int) -> "PostgresSelectStmt":
        self._offset = n
        return self

    def build(self) -> SqlStatement:
        if self._table is None:
            raise ValueError(
                "select().from_table(<name>) is required before build()"
            )

        cols = ", ".join(self._columns) if self._columns else "*"
        where_sql, where_params = self._where_sql(self._where, start_n=1)

        parts = [f"SELECT {cols} FROM {self._table}"]
        if where_sql:
            parts.append(f"WHERE {where_sql}")
        if self._order_by:
            parts.append(f"ORDER BY {self._order_by}")
        if self._limit is not None:
            parts.append(f"LIMIT {self._limit}")
        if self._offset is not None:
            parts.append(f"OFFSET {self._offset}")

        return SqlStatement("\n".join(parts), where_params)


class _SqliteCommon:
    """Shared formatting helpers for SQLite staged builders."""

    style = "?"

    @staticmethod
    def _returning_clause(*cols: str) -> str:
        # SQLite learned RETURNING in 3.35 (2021); all modern builds have it.
        return "RETURNING " + (", ".join(cols) if cols else "*")

    @staticmethod
    def _where_sql(where: WhereClause) -> Tuple[str, Tuple[Any, ...]]:
        """Render ``where`` to a ``?``-numbered SQL fragment.

        Returns ``(sql_fragment, bound_params)``.  ORDER MATTERS:
        the AND group is emitted first, then the OR group, so the
        param tuple matches the placeholder order.
        """
        if where.is_empty:
            return "", ()

        parts: List[str] = []
        params: List[Any] = []

        if where.and_pairs:
            sub = " AND ".join(f"{col} = ?" for col, _ in where.and_pairs)
            parts.append(f"({sub})")
            params.extend(v for _, v in where.and_pairs)

        if where.or_pairs:
            sub = " OR ".join(f"{col} = ?" for col, _ in where.or_pairs)
            parts.append(f"({sub})")
            params.extend(v for _, v in where.or_pairs)

        return " AND ".join(parts), tuple(params)


class SqliteInsertStmt(_SqliteCommon, InsertStmtABC):
    """SQLite ``INSERT`` staged builder."""

    def __init__(self) -> None:
        self._table: Optional[str] = None
        self._columns: List[str] = []
        self._values: List[Any] = []
        self._on_conflict = ""
        self._returning: Optional[Tuple[str, ...]] = None

    def into(self, table: str) -> "SqliteInsertStmt":
        self._table = table
        return self

    def columns(self, *cols: str) -> "SqliteInsertStmt":
        self._columns = list(cols)
        return self

    def values(self, *positional: Any, **kwargs: Any) -> "SqliteInsertStmt":
        if positional and kwargs:
            raise TypeError("values() takes positional or keyword args, not both")
        if kwargs:
            self._columns = list(kwargs.keys())
            self._values = list(kwargs.values())
        else:
            self._values = list(positional)
        return self

    def on_conflict(self, fragment: str) -> "SqliteInsertStmt":
        self._on_conflict = fragment
        return self

    def returning(self, *cols: str) -> "SqliteInsertStmt":
        self._returning = tuple(cols) if cols else ("*",)
        return self

    def build(self) -> SqlStatement:
        if self._table is None:
            raise ValueError("insert().into(<table>) is required before build()")
        if not self._columns:
            raise ValueError("insert() needs either values(...) or columns(...)")

        placeholders = ["?"] * len(self._values)
        parts = [
            f"INSERT INTO {self._table} ({', '.join(self._columns)})",
            f"VALUES ({', '.join(placeholders)})",
        ]
        if self._on_conflict:
            parts.append(f"ON CONFLICT {self._on_conflict}")
        if self._returning:
            parts.append(self._returning_clause(*self._returning))

        return SqlStatement("\n".join(parts) + "\n", tuple(self._values))


class SqliteUpdateStmt(_SqliteCommon, UpdateStmtABC):
    """SQLite ``UPDATE`` staged builder."""

    def __init__(self) -> None:
        self._table: Optional[str] = None
        self._set: List[Tuple[str, Any]] = []
        self._where = WhereClause()
        self._returning: Optional[Tuple[str, ...]] = None

    def table(self, name: str) -> "SqliteUpdateStmt":
        self._table = name
        return self

    def set(self, **kwargs: Any) -> "SqliteUpdateStmt":
        self._set = list(kwargs.items())
        return self

    def where(
        self,
        and_: Optional[Dict[str, Any]] = None,
        or_: Optional[Dict[str, Any]] = None,
    ) -> "SqliteUpdateStmt":
        self._where = WhereClause.build(and_=and_, or_=or_)
        return self

    def returning(self, *cols: str) -> "SqliteUpdateStmt":
        self._returning = tuple(cols) if cols else ("*",)
        return self

    def build(self) -> SqlStatement:
        if self._table is None:
            raise ValueError("update().table(<name>) is required before build()")
        if not self._set:
            raise ValueError("update() needs at least one column in set(...)")

        set_sql = ", ".join(f"{col} = ?" for col, _ in self._set)

        where_sql, where_params = self._where_sql(self._where)

        parts = [f"UPDATE {self._table}", f"SET {set_sql}"]
        if where_sql:
            parts.append(f"WHERE {where_sql}")
        if self._returning:
            parts.append(self._returning_clause(*self._returning))

        set_values = [v for _, v in self._set]
        return SqlStatement("\n".join(parts) + "\n", tuple(set_values) + where_params)


class SqliteDeleteStmt(_SqliteCommon, DeleteStmtABC):
    """SQLite ``DELETE`` staged builder."""

    def __init__(self) -> None:
        self._table: Optional[str] = None
        self._where = WhereClause()
        self._returning: Optional[Tuple[str, ...]] = None

    def from_table(self, name: str) -> "SqliteDeleteStmt":
        self._table = name
        return self

    def where(
        self,
        and_: Optional[Dict[str, Any]] = None,
        or_: Optional[Dict[str, Any]] = None,
    ) -> "SqliteDeleteStmt":
        self._where = WhereClause.build(and_=and_, or_=or_)
        return self

    def returning(self, *cols: str) -> "SqliteDeleteStmt":
        self._returning = tuple(cols) if cols else ("*",)
        return self

    def build(self) -> SqlStatement:
        if self._table is None:
            raise ValueError(
                "delete().from_table(<name>) is required before build()"
            )

        where_sql, where_params = self._where_sql(self._where)

        parts = [f"DELETE FROM {self._table}"]
        if where_sql:
            parts.append(f"WHERE {where_sql}")
        if self._returning:
            parts.append(self._returning_clause(*self._returning))

        return SqlStatement("\n".join(parts) + "\n", where_params)


class SqliteSelectStmt(_SqliteCommon, SelectStmtABC):
    """SQLite ``SELECT`` staged builder."""

    def __init__(self) -> None:
        self._columns: Tuple[str, ...] = ()
        self._table: Optional[str] = None
        self._where = WhereClause()
        self._order_by: Optional[str] = None
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None

    def columns(self, *cols: str) -> "SqliteSelectStmt":
        self._columns = cols
        return self

    def from_table(self, name: str) -> "SqliteSelectStmt":
        self._table = name
        return self

    def where(
        self,
        and_: Optional[Dict[str, Any]] = None,
        or_: Optional[Dict[str, Any]] = None,
    ) -> "SqliteSelectStmt":
        self._where = WhereClause.build(and_=and_, or_=or_)
        return self

    def order_by(self, fragment: str) -> "SqliteSelectStmt":
        self._order_by = fragment
        return self

    def limit(self, n: int) -> "SqliteSelectStmt":
        self._limit = n
        return self

    def offset(self, n: int) -> "SqliteSelectStmt":
        self._offset = n
        return self

    def build(self) -> SqlStatement:
        if self._table is None:
            raise ValueError(
                "select().from_table(<name>) is required before build()"
            )

        cols = ", ".join(self._columns) if self._columns else "*"
        where_sql, where_params = self._where_sql(self._where)

        parts = [f"SELECT {cols} FROM {self._table}"]
        if where_sql:
            parts.append(f"WHERE {where_sql}")
        if self._order_by:
            parts.append(f"ORDER BY {self._order_by}")
        if self._limit is not None:
            parts.append(f"LIMIT {self._limit}")
        if self._offset is not None:
            parts.append(f"OFFSET {self._offset}")

        return SqlStatement("\n".join(parts), where_params)
