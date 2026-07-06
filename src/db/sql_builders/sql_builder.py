"""Top-level entry points for the SQL builder package.

These are the classes a caller touches directly:

* :class:`SqlBuilderABC` -- the abstract entry point.  Subclasses
  are dialect-specific and are obtained from
  :class:`SqlBuilderFactory`.
* :class:`PostgresSqlBuilder` -- Postgres flavour, returns
  :class:`PostgresInsertStmt` / ``Update`` / ``Delete`` /
  ``Select`` from the fluent entry methods.
* :class:`SqliteSqlBuilder` -- SQLite flavour, returns
  :class:`SqliteInsertStmt` / ``Update`` / ``Delete`` / ``Select``
  with the same fluent surface.

Usage::

    builder = SqlBuilderFactory.create("postgres", name="users")
    stmt = (
        builder.insert()
        .into("users")
        .values(id="u-1", username="kurama")
        .returning("id")
        .build()
    )

The fluent staged builders live in
:mod:`src.db.sql_builders.statements`.  They implement the four
abstract bases (:class:`InsertStmtABC`, :class:`UpdateStmtABC`,
:class:`DeleteStmtABC`, :class:`SelectStmtABC`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Sequence

from src.db.sql_builders.sql_statement import SqlStatement
from src.db.sql_builders.statements import (
    DeleteStmtABC,
    InsertStmtABC,
    PostgresDeleteStmt,
    PostgresInsertStmt,
    PostgresSelectFromStmt,
    PostgresSelectStmt,
    PostgresUpdateStmt,
    SelectFromStmtABC,
    SelectStmtABC,
    SqliteDeleteStmt,
    SqliteInsertStmt,
    SqliteSelectFromStmt,
    SqliteSelectStmt,
    SqliteUpdateStmt,
    UpdateStmtABC,
)
from src.db.sql_builders.where_clause import WhereClause


class SqlBuilderABC(ABC):
    """Entry point for the fluent SQL builder API for one dialect.

    A :class:`SqlBuilderABC` is the object you obtain once per dialect
    (via :class:`SqlBuilderFactory`) and then keep around to build
    statements from.  Concrete subclasses -- :class:`PostgresSqlBuilder`
    and :class:`SqliteSqlBuilder` -- implement the four fluent entry
    methods (:meth:`insert`, :meth:`update`, :meth:`delete`,
    :meth:`select`); each returns a "staged builder" you chain on
    before calling ``.build()`` to materialise the final
    :class:`SqlStatement`.

    The split mirrors what you'd find in JOOQ, knex, or
    sqlalchemy-core: the abstract base owns the *chain shape*
    (the methods you call and the order you call them), while the
    per-dialect subclasses own the *placeholder style* (``$1`` vs
    ``?``) and any dialect-specific syntax quirks.

    Usage
    -----

    Pick a dialect once and hold onto the builder::

        from src.db.sql_builders import SqlBuilderFactory

        builder = SqlBuilderFactory.create("postgres", name="users")

    All four statement kinds share the same fluent shape:
    ``builder.<kind>().<chain>().build()``.

    INSERT::

        stmt = (
            builder.insert()
            .into("users")
            .values(id="u-1", username="kurama", email="k@example")
            .on_conflict("(id) DO NOTHING")
            .returning("id")
            .build()
        )
        # -> SqlStatement(
        #     "INSERT INTO users (id, username, email)\n"
        #     "VALUES ($1, $2, $3)\n"
        #     "ON CONFLICT (id) DO NOTHING\n"
        #     "RETURNING id\n",
        #     ("u-1", "kurama", "k@example"),
        # )

    UPDATE -- with an AND group and an OR group in WHERE::

        stmt = (
            builder.update()
            .table("users")
            .set(username="newname", email="new@example")
            .where(
                and_={"active": True, "verified": True},
                or_={"region": "EU", "country": "DE"},
            )
            .returning()
            .build()
        )
        # -> SqlStatement(
        #     "UPDATE users\n"
        #     "SET username = $1, email = $2\n"
        #     "WHERE (active = $3 AND verified = $4)\n"
        #     "       AND (region = $5 OR country = $6)\n"
        #     "RETURNING *\n",
        #     ("newname", "new@example", True, True, "EU", "DE"),
        # )

    DELETE::

        stmt = (
            builder.delete()
            .from_table("users")
            .where(and_={"id": "u-1"})
            .returning("id", "username")
            .build()
        )

    SELECT -- with explicit columns, order_by, limit and offset::

        stmt = (
            builder.select()
            .columns("id", "username")
            .from_table("users")
            .where(
                and_={"status": "active"},
                or_={"region": "EU", "country": "DE"},
            )
            .order_by("created_at DESC")
            .limit(10)
            .offset(20)
            .build()
        )

    Raw escape hatch -- ``fetch`` wraps a caller-supplied SQL string
    + bound params and is what :class:`src.db.table.Table.fetch`
    funnels through when a fluent shape can't capture the intent::

        stmt = builder.fetch(
            "SELECT * FROM users WHERE legacy_flag = ?",
            [True],
        )

    WHERE keyword gotcha
    --------------------

    ``and`` and ``or`` are Python reserved keywords, so the WHERE
    kwargs on every staged builder use a trailing underscore:
    ``and_=`` and ``or_``.  Both groups are optional; passing
    neither leaves the statement with no WHERE clause.

    See Also
    --------

    * :class:`SqlStatement` -- the value returned by ``.build()``.
    * :class:`WhereClause` -- the value object that actually holds
      the AND/OR groups rendered into SQL.
    * :class:`InsertStmtABC`, :class:`UpdateStmtABC`,
      :class:`DeleteStmtABC`, :class:`SelectStmtABC` -- the staged
      builders returned by the fluent entry methods.
    * :class:`SqlBuilderFactory` -- the canonical way to obtain a
      builder for one dialect.
    """

    name: str  # set by SqlBuilderFactory.create(name=...)

    @abstractmethod
    def insert(self) -> InsertStmtABC:
        """Start a fluent ``INSERT`` statement."""

    @abstractmethod
    def update(self) -> UpdateStmtABC:
        """Start a fluent ``UPDATE`` statement."""

    @abstractmethod
    def delete(self) -> DeleteStmtABC:
        """Start a fluent ``DELETE`` statement."""

    @abstractmethod
    def select(self) -> SelectStmtABC:
        """Start a fluent ``SELECT`` statement.

        The staged builder's :meth:`SelectStmtABC.from_table`
        decides which table the statement targets.
        """

    @abstractmethod
    def select_from(self, table: str) -> "SelectFromStmtABC":
        """Start a fluent ``SELECT ... FROM <table> WHERE <WhereClause>``.

        Use this when the WHERE clause carries pair shapes the
        dict-based :meth:`SelectStmtABC.where` can't express --
        ``IS NULL``, raw ``>= $N`` fragments, ``IN ($N, $N+1)``
        lists, OR groups, ...  Build the :class:`WhereClause`
        incrementally via :meth:`WhereClause.add_and` /
        :meth:`WhereClause.add_or`, then bind it with
        :meth:`SelectFromStmtABC.where_clause`.

        The chain shape mirrors :class:`SelectStmtABC`: ``columns``,
        ``where_clause``, ``order_by``, ``limit``, ``offset``,
        ``group_by``, ``build``.

        Example::

            clause = WhereClause.empty() \\
                .add_and(("note_id", "n-1")) \\
                .add_or(("directory_id", ["d-1", "d-2"]))

            stmt = (
                builder.select_from("activity")
                    .columns("id", "actor_id", "at")
                    .where_clause(clause)
                    .order_by("at DESC")
                    .limit(50)
                    .build()
            )
        """

    @abstractmethod
    def fetch(self, sql: str, args: Sequence[object]) -> SqlStatement:
        """Wrap a caller-supplied ``sql``/``args`` into a :class:`SqlStatement`.

        Pass-through; the legacy :meth:`src.db.table.Table.fetch`
        escape hatch relies on it.
        """

    @abstractmethod
    def supports_returning(self) -> bool:
        """Whether this dialect emits ``RETURNING`` natively."""


class PostgresSqlBuilder(SqlBuilderABC):
    """Postgres flavour of :class:`SqlBuilderABC`."""

    def insert(self) -> PostgresInsertStmt:
        return PostgresInsertStmt()

    def update(self) -> PostgresUpdateStmt:
        return PostgresUpdateStmt()

    def delete(self) -> PostgresDeleteStmt:
        return PostgresDeleteStmt()

    def select(self) -> PostgresSelectStmt:
        return PostgresSelectStmt()

    def select_from(self, table: str) -> "PostgresSelectFromStmt":
        return PostgresSelectFromStmt(table)

    def fetch(self, sql: str, args: Sequence[object]) -> SqlStatement:
        return SqlStatement(sql, tuple(args))

    def supports_returning(self) -> bool:
        return True


class SqliteSqlBuilder(SqlBuilderABC):
    """SQLite flavour of :class:`SqlBuilderABC`."""

    def insert(self) -> SqliteInsertStmt:
        return SqliteInsertStmt()

    def update(self) -> SqliteUpdateStmt:
        return SqliteUpdateStmt()

    def delete(self) -> SqliteDeleteStmt:
        return SqliteDeleteStmt()

    def select(self) -> SqliteSelectStmt:
        return SqliteSelectStmt()

    def select_from(self, table: str) -> "SqliteSelectFromStmt":
        return SqliteSelectFromStmt(table)

    def fetch(self, sql: str, args: Sequence[object]) -> SqlStatement:
        return SqlStatement(sql, tuple(args))

    def supports_returning(self) -> bool:
        return True


class SqlBuilderFactory:
    """Factory that returns a fresh :class:`SqlBuilderABC` for a dialect.

    Usage::

        builder = SqlBuilderFactory.create("postgres", name="users")
        statement = builder.insert().into("users").values(id="u-1").build()

    Use :meth:`supported_dialects` to enumerate the known dialects --
    useful for config validation in startup paths.
    """

    _REGISTRY: Dict[str, type] = {
        "postgres": PostgresSqlBuilder,
        "sqlite": SqliteSqlBuilder,
    }

    @classmethod
    def create(cls, dialect: str, name: str = "") -> SqlBuilderABC:
        """Return a builder for ``dialect``.

        Args:
            dialect: one of :meth:`supported_dialects`.
            name: optional table name to bind.  Most callers set it
                once and let the fluent staged builders inherit
                ``self.name`` from the builder.  Passed to the
                staged builder via ``IntoBuilder.into(table)`` only
                when the caller wants to override.

        Returns:
            A fresh :class:`SqlBuilderABC` instance.

        Raises:
            ValueError: if ``dialect`` is not registered.
        """
        try:
            builder_cls = cls._REGISTRY[dialect]
        except KeyError as exc:
            raise ValueError(
                f"Unknown SQL dialect {dialect!r}; "
                f"expected one of {sorted(cls._REGISTRY)}"
            ) from exc
        builder = builder_cls()
        builder.name = name
        return builder

    @classmethod
    def supported_dialects(cls) -> List[str]:
        """Return the list of dialect names the factory knows about."""
        return list(cls._REGISTRY)


__all__ = [
    "PostgresSqlBuilder",
    "SqlBuilderABC",
    "SqlBuilderFactory",
    "SqliteSqlBuilder",
]
