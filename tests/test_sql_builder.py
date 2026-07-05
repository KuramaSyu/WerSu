"""Unit tests for the fluent SQL builder API.

Each test instantiates a builder, calls the chainable methods that
matter for one statement kind, then asserts on the resulting SQL
string and bound parameters.  No database connection is required --
that's the whole point of separating the builder from the executor.
"""

from __future__ import annotations

import pytest

from src.db.sql_builders import (
    DeleteStmtABC,
    InsertStmtABC,
    PostgresSqlBuilder,
    SelectStmtABC,
    SqlBuilderABC,
    SqlBuilderFactory,
    SqlStatement,
    SqliteSqlBuilder,
    UpdateStmtABC,
    WhereClause,
)


# Postgres ---------------------------------------------------------------


class TestPostgresInsertBuilder:
    """``PostgresInsertStmt`` must emit ``$n`` placeholders and ``RETURNING``."""

    def test_keyword_values_build_full_insert(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = (
            builder.insert()
            .into("users")
            .values(id="u-1", username="kurama")
            .returning("id")
            .build()
        )

        assert isinstance(stmt, SqlStatement)
        assert stmt.sql == (
            "INSERT INTO users (id, username)\n"
            "VALUES ($1, $2)\n"
            "RETURNING id\n"
        )
        assert stmt.params == ("u-1", "kurama")

    def test_positional_values_require_columns(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = (
            builder.insert()
            .into("users")
            .columns("id", "username")
            .values("u-1", "kurama")
            .build()
        )

        assert "VALUES ($1, $2)" in stmt.sql
        assert stmt.params == ("u-1", "kurama")

    def test_on_conflict_appends_raw_fragment(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = (
            builder.insert()
            .into("users")
            .values(id="u-1")
            .on_conflict("(id) DO NOTHING")
            .build()
        )

        assert "ON CONFLICT (id) DO NOTHING" in stmt.sql

    def test_returning_with_no_args_uses_star(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = (
            builder.insert()
            .into("users")
            .values(id="u-1")
            .returning()
            .build()
        )

        assert "RETURNING *" in stmt.sql

    def test_build_without_into_raises(self) -> None:
        builder = PostgresSqlBuilder()
        with pytest.raises(ValueError, match="into"):
            builder.insert().values(id="u-1").build()

    def test_build_without_values_raises(self) -> None:
        builder = PostgresSqlBuilder()
        with pytest.raises(ValueError, match="values"):
            builder.insert().into("users").build()


class TestPostgresUpdateBuilder:
    """``PostgresUpdateStmt`` chains ``set`` and ``where`` fluently."""

    def test_set_with_where_renders_set_and_where(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = (
            builder.update()
            .table("users")
            .set(username="kurama", email="k@example")
            .where(and_={"id": "u-1"})
            .build()
        )

        assert stmt.sql == (
            "UPDATE users\n"
            "SET username = $1, email = $2\n"
            "WHERE (id = $3)\n"
        )
        assert stmt.params == ("kurama", "k@example", "u-1")

    def test_where_combines_and_and_or_groups(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = (
            builder.update()
            .table("users")
            .set(username="new")
            .where(and_={"active": True}, or_={"region": "EU"})
            .build()
        )

        assert "WHERE (active = $2) AND (region = $3)" in stmt.sql
        assert stmt.params == ("new", True, "EU")


class TestPostgresDeleteBuilder:
    """``PostgresDeleteStmt`` is small: table, optional where, optional returning."""

    def test_delete_with_returning(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = (
            builder.delete()
            .from_table("users")
            .where(and_={"id": "u-1"})
            .returning("id", "username")
            .build()
        )

        assert stmt.sql == (
            "DELETE FROM users\n"
            "WHERE (id = $1)\n"
            "RETURNING id, username\n"
        )
        assert stmt.params == ("u-1",)

    def test_delete_without_where_skips_clause(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = builder.delete().from_table("users").returning().build()

        assert "WHERE" not in stmt.sql
        assert "RETURNING *" in stmt.sql


class TestPostgresSelectBuilder:
    """``PostgresSelectStmt`` carries order_by / limit / offset on top of where."""

    def test_select_with_columns_and_order_by(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = (
            builder.select()
            .columns("id", "name")
            .from_table("users")
            .where(and_={"status": "active"})
            .order_by("created_at DESC")
            .build()
        )

        assert stmt.sql == (
            "SELECT id, name FROM users\n"
            "WHERE (status = $1)\n"
            "ORDER BY created_at DESC"
        )
        assert stmt.params == ("active",)

    def test_select_where_with_or_group(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = (
            builder.select()
            .from_table("users")
            .where(and_={"active": True}, or_={"region": "EU", "country": "DE"})
            .build()
        )

        assert "WHERE (active = $1) AND (region = $2 OR country = $3)" in stmt.sql
        assert stmt.params == (True, "EU", "DE")

    def test_select_default_columns_is_star(self) -> None:
        builder = PostgresSqlBuilder()
        stmt = builder.select().from_table("users").where(and_={"id": "u-1"}).build()

        assert stmt.sql.startswith("SELECT * FROM users")


# SQLite ------------------------------------------------------------------


class TestSqliteInsertBuilder:
    """``SqliteInsertStmt`` uses ``?`` placeholders, identical chain."""

    def test_question_mark_placeholders(self) -> None:
        builder = SqliteSqlBuilder()
        stmt = (
            builder.insert()
            .into("users")
            .values(id="u-1", username="kurama")
            .returning("id")
            .build()
        )

        assert stmt.sql == (
            "INSERT INTO users (id, username)\n"
            "VALUES (?, ?)\n"
            "RETURNING id\n"
        )
        assert stmt.params == ("u-1", "kurama")

    def test_update_uses_question_marks(self) -> None:
        builder = SqliteSqlBuilder()
        stmt = (
            builder.update()
            .table("users")
            .set(username="kurama")
            .where(and_={"id": "u-1"})
            .build()
        )

        assert stmt.sql == (
            "UPDATE users\n"
            "SET username = ?\n"
            "WHERE (id = ?)\n"
        )
        assert stmt.params == ("kurama", "u-1")

    def test_delete_emits_question_marks(self) -> None:
        builder = SqliteSqlBuilder()
        stmt = (
            builder.delete()
            .from_table("users")
            .where(and_={"id": "u-1"})
            .build()
        )

        assert stmt.sql == "DELETE FROM users\nWHERE (id = ?)\n"
        assert stmt.params == ("u-1",)


# SqlBuilderFactory -------------------------------------------------------


class TestSqlBuilderFactory:
    """``SqlBuilderFactory.create`` is the only entry callers should use."""

    def test_postgres_returns_postgres_builder(self) -> None:
        builder = SqlBuilderFactory.create("postgres", name="users")

        assert isinstance(builder, PostgresSqlBuilder)
        assert builder.name == "users"

    def test_sqlite_returns_sqlite_builder(self) -> None:
        builder = SqlBuilderFactory.create("sqlite", name="users")

        assert isinstance(builder, SqliteSqlBuilder)
        assert builder.name == "users"

    def test_unknown_dialect_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown SQL dialect"):
            SqlBuilderFactory.create("oracle", name="users")

    def test_supported_dialects_lists_known(self) -> None:
        dialects = SqlBuilderFactory.supported_dialects()

        assert "postgres" in dialects
        assert "sqlite" in dialects

    def test_factory_returns_concrete_subclass_of_abc(self) -> None:
        # Catches a future regression where someone adds a builder
        # class without registering it in the factory registry.
        for dialect in SqlBuilderFactory.supported_dialects():
            builder = SqlBuilderFactory.create(dialect, name="users")

            assert isinstance(builder, SqlBuilderABC)
            # Every fluent entry returns a real staged builder, not
            # a None -- if any of these return None we silently
            # lose every Table call routed through them.
            assert isinstance(builder.insert(), InsertStmtABC)
            assert isinstance(builder.update(), UpdateStmtABC)
            assert isinstance(builder.delete(), DeleteStmtABC)
            assert isinstance(builder.select(), SelectStmtABC)


# WhereClause ------------------------------------------------------------


class TestWhereClause:
    """``WhereClause`` composes an AND group with an OR group."""

    def test_build_with_no_args_is_empty(self) -> None:
        clause = WhereClause.build()

        assert clause.is_empty
        assert clause.total_params() == 0

    def test_build_with_and_group(self) -> None:
        clause = WhereClause.build(and_={"status": "active", "verified": True})

        assert not clause.is_empty
        assert clause.total_params() == 2
        assert clause.and_pairs == (("status", "active"), ("verified", True))

    def test_build_with_or_group(self) -> None:
        clause = WhereClause.build(or_={"region": "EU"})

        assert clause.or_pairs == (("region", "EU"),)
        assert clause.and_pairs == ()

    def test_direct_construction_keeps_duplicate_columns(self) -> None:
        # The fluent ``build()`` helper collapses duplicates because
        # it takes a dict; the underlying class accepts tuples so
        # advanced callers can repeat a column with two different
        # values inside the same group.
        clause = WhereClause(
            and_pairs=(("status", "active"), ("status", "pending")),
        )

        assert clause.and_pairs == (
            ("status", "active"),
            ("status", "pending"),
        )
        assert clause.total_params() == 2
