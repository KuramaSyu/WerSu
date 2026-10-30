"""Tests for :class:`WhereClause` and the per-dialect ``render_where``.

The builder package's existing ``test_sql_builder.py`` covers the
plain equality / AND / OR cases for the staged ``select()``
builder.  These tests focus on the extensions that support the
activity repo:

* :meth:`WhereClause.add_and` / :meth:`WhereClause.add_or`
* ``IS NULL`` semantics for ``None`` values
* raw-SQL pairs (``>= $N``)
* list pairs (``column IN ($N, $N+1, ...)``)
* the unified :func:`render_where` helper

Both dialects are covered; the ``cross-dialect`` parametrized test
verifies the param tuple is identical regardless of placeholder
style.
"""

from __future__ import annotations

import pytest

from src.db.sql_builders import (
    PostgresSqlBuilder,
    SqliteSqlBuilder,
    WhereClause,
    WherePair,
    is_is_null_pair,
    is_raw_pair,
    render_where,
)


# --------------------------------------------------------------------------
# WhereClause.add_and / add_or
# --------------------------------------------------------------------------


class TestAddAndAddOr:
    """``add_and`` / ``add_or`` grow the clause incrementally."""

    def test_add_and_returns_new_instance(self) -> None:
        """The original clause is left untouched."""
        original = WhereClause.empty()
        grown = original.add_and(("note_id", "n-1"))
        assert original.and_pairs == ()
        assert grown.and_pairs == (("note_id", "n-1"),)

    def test_add_and_chains(self) -> None:
        """Multiple ``add_and`` calls accumulate pairs in order."""
        clause = (
            WhereClause.empty()
            .add_and(("note_id", "n-1"))
            .add_and(("actor_id", "alice"))
            .add_and(("at >= $N", "2024-01-01"))
        )
        assert len(clause.and_pairs) == 3
        assert clause.and_pairs[0] == ("note_id", "n-1")
        assert clause.and_pairs[1] == ("actor_id", "alice")
        assert clause.and_pairs[2] == ("at >= $N", "2024-01-01")

    def test_add_or_grows_or_group(self) -> None:
        """``add_or`` puts pairs in the OR group, leaving AND alone."""
        clause = (
            WhereClause.empty()
            .add_and(("note_id", "n-1"))
            .add_or(("directory_id", ["d-1", "d-2"]))
        )
        assert clause.and_pairs == (("note_id", "n-1"),)
        assert clause.or_pairs == (("directory_id", ["d-1", "d-2"]),)

    def test_add_and_preserves_or_group(self) -> None:
        """Adding to AND does not disturb existing OR pairs."""
        clause = (
            WhereClause.empty()
            .add_or(("note_id", ["a", "b"]))
            .add_and(("role", "writer"))
        )
        assert clause.and_pairs == (("role", "writer"),)
        assert clause.or_pairs == (("note_id", ["a", "b"]),)

    def test_is_empty_still_works_after_add(self) -> None:
        """``is_empty`` reflects both groups after additions."""
        assert WhereClause.empty().is_empty is True
        assert WhereClause.empty().add_and(("id", "x")).is_empty is False
        assert WhereClause.empty().add_or(("id", "x")).is_empty is False


# --------------------------------------------------------------------------
# Pair classification helpers
# --------------------------------------------------------------------------


class TestPairDetection:
    """``is_is_null_pair`` / ``is_raw_pair`` classify each pair shape."""

    def test_plain_value_pair_is_neither(self) -> None:
        pair: WherePair = ("id", "x")
        assert is_is_null_pair(pair) is False
        assert is_raw_pair(pair) is False

    def test_none_value_pair_is_is_null(self) -> None:
        pair: WherePair = ("actor_id", None)
        assert is_is_null_pair(pair) is True
        assert is_raw_pair(pair) is False

    def test_explicit_is_null_three_tuple(self) -> None:
        pair: WherePair = ("actor_id", None, True)
        assert is_is_null_pair(pair) is True

    def test_explicit_equality_three_tuple(self) -> None:
        pair: WherePair = ("actor_id", None, False)
        assert is_is_null_pair(pair) is False

    def test_raw_pair_detected_by_dollar(self) -> None:
        pair: WherePair = ("at >= $N", "2024-01-01")
        assert is_raw_pair(pair) is True

    def test_raw_pair_detected_by_question_mark(self) -> None:
        pair: WherePair = ("at >= ?", "2024-01-01")
        assert is_raw_pair(pair) is True


# --------------------------------------------------------------------------
# WhereClause.total_params
# --------------------------------------------------------------------------


class TestTotalParams:
    """``WhereClause.total_params`` ignores IS NULL pairs."""

    def test_empty_clause_consumes_zero(self) -> None:
        assert WhereClause().total_params() == 0

    def test_concrete_pairs_consume_one_each(self) -> None:
        clause = WhereClause(and_pairs=(("id", "x"), ("role", "writer")))
        assert clause.total_params() == 2

    def test_is_null_pair_consumes_zero(self) -> None:
        clause = WhereClause(and_pairs=(("actor_id", None),))
        assert clause.total_params() == 0

    def test_mixed_pairs_count_concrete_only(self) -> None:
        clause = WhereClause(
            and_pairs=(
                ("note_id", "n-1"),
                ("actor_id", None),
                ("role", "writer"),
            ),
            or_pairs=(),
        )
        assert clause.total_params() == 2

    def test_or_pairs_counted_too(self) -> None:
        clause = WhereClause(
            and_pairs=(),
            or_pairs=(("note_id", "n-1"), ("directory_id", "d-1")),
        )
        assert clause.total_params() == 2


# --------------------------------------------------------------------------
# render_where -- both dialects
# --------------------------------------------------------------------------


# (and_pairs, or_pairs, dialect, expected_sql, expected_params)
_RENDER_CASES = [
    pytest.param((), (), "postgres", "", (), id="empty-postgres"),
    pytest.param((), (), "sqlite", "", (), id="empty-sqlite"),
    pytest.param(
        (("id", "x"),), (), "postgres", "(id = $1)", ("x",), id="equality-postgres"
    ),
    pytest.param(
        (("id", "x"),), (), "sqlite", "(id = ?)", ("x",), id="equality-sqlite"
    ),
    pytest.param(
        (("a", 1), ("b", 2), ("c", 3)),
        (),
        "postgres",
        "(a = $1 AND b = $2 AND c = $3)",
        (1, 2, 3),
        id="multiple-and-pairs-postgres",
    ),
    pytest.param(
        (("actor_id", None),),
        (),
        "postgres",
        "(actor_id IS NULL)",
        (),
        id="is-null-postgres",
    ),
    pytest.param(
        (("actor_id", None),),
        (),
        "sqlite",
        "(actor_id IS NULL)",
        (),
        id="is-null-sqlite",
    ),
    pytest.param(
        (("note_id", "n-1"), ("actor_id", None), ("role", "writer")),
        (),
        "postgres",
        "(note_id = $1 AND actor_id IS NULL AND role = $2)",
        ("n-1", "writer"),
        id="mixed-concrete-and-is-null-postgres",
    ),
    pytest.param(
        (("at >= $N", "2024-01-01"),),
        (),
        "postgres",
        "(at >= $1)",
        ("2024-01-01",),
        id="raw-dollar-postgres",
    ),
    pytest.param(
        (("at >= ?", "2024-01-01"),),
        (),
        "sqlite",
        "(at >= ?)",
        ("2024-01-01",),
        id="raw-question-sqlite",
    ),
    pytest.param(
        (("at >= $N", "2024-01-01"),),
        (),
        "sqlite",
        "(at >= ?)",
        ("2024-01-01",),
        id="raw-dollar-to-question-sqlite",
    ),
    pytest.param(
        (("action", ["a", "b", "c"]),),
        (),
        "postgres",
        "(action IN ($1, $2, $3))",
        ("a", "b", "c"),
        id="list-in-postgres",
    ),
    pytest.param(
        (("action", ["a", "b"]),),
        (),
        "sqlite",
        "(action IN (?, ?))",
        ("a", "b"),
        id="list-in-sqlite",
    ),
    pytest.param(
        (("action", []),),
        (),
        "postgres",
        "(FALSE)",
        (),
        id="empty-list-postgres",
    ),
    pytest.param(
        (("note_id", "n-1"), ("at >= $N", "2024-01-01")),
        (("actor_id", "alice"), ("actor_id", "bob")),
        "postgres",
        "(note_id = $1 AND at >= $2) AND (actor_id = $3 OR actor_id = $4)",
        ("n-1", "2024-01-01", "alice", "bob"),
        id="combined-and-or-postgres",
    ),
    pytest.param(
        (("a", 1),),
        (("b", 2),),
        "postgres",
        "(a = $1) AND (b = $2)",
        (1, 2),
        id="placeholder-numbering-crosses-groups-postgres",
    ),
    pytest.param(
        (),
        (("note_id", ["n-1", "n-2"]), ("directory_id", ["d-1"])),
        "postgres",
        "(note_id IN ($1, $2) OR directory_id IN ($3))",
        ("n-1", "n-2", "d-1"),
        id="list-in-or-group-postgres",
    ),
    pytest.param(
        (),
        (("note_id", ["n-1", "n-2"]), ("directory_id", ["d-1"])),
        "sqlite",
        "(note_id IN (?, ?) OR directory_id IN (?))",
        ("n-1", "n-2", "d-1"),
        id="list-in-or-group-sqlite",
    ),
]


@pytest.mark.parametrize(
    ("and_pairs", "or_pairs", "dialect", "expected_sql", "expected_params"),
    _RENDER_CASES,
)
def test_render_where(
    and_pairs, or_pairs, dialect, expected_sql, expected_params
) -> None:
    """``render_where`` renders the same clause shapes on both dialects."""
    sql, params = render_where(
        WhereClause(and_pairs=and_pairs, or_pairs=or_pairs),
        dialect=dialect,
    )
    assert sql == expected_sql
    assert params == expected_params


# --------------------------------------------------------------------------
# select_from -- staged builder integration
# --------------------------------------------------------------------------


class TestSelectFromStagedBuilder:
    """``builder.select_from(...)`` chains like the other staged builders."""

    def test_postgres_staged_chain(self) -> None:
        builder = PostgresSqlBuilder()
        clause = WhereClause.empty().add_and(("note_id", "n-1"))
        stmt = (
            builder.select_from("activity")
            .columns("id", "actor_id", "at")
            .where_clause(clause)
            .order_by("at DESC")
            .limit(50)
            .offset(10)
            .build()
        )
        # ``WHERE`` consumes ``$1``, ``LIMIT`` consumes ``$2``, ``OFFSET``
        # consumes ``$3`` -- the staged builder numbers them as it
        # appends the bound params.
        assert stmt.sql == (
            "SELECT id, actor_id, at FROM activity\n"
            "WHERE (note_id = $1)\n"
            "ORDER BY at DESC\n"
            "LIMIT $2\n"
            "OFFSET $3"
        )
        assert stmt.params == ("n-1", 50, 10)

    def test_sqlite_staged_chain(self) -> None:
        builder = SqliteSqlBuilder()
        clause = WhereClause.empty().add_and(("note_id", "n-1"))
        stmt = (
            builder.select_from("activity")
            .columns("id", "at")
            .where_clause(clause)
            .limit(5)
            .build()
        )
        assert stmt.sql == (
            "SELECT id, at FROM activity\n"
            "WHERE (note_id = ?)\n"
            "LIMIT ?"
        )
        assert stmt.params == ("n-1", 5)

    def test_postgres_group_by_chain(self) -> None:
        """Aggregate SELECT emits GROUP BY between WHERE and ORDER BY."""
        builder = PostgresSqlBuilder()
        clause = WhereClause.empty().add_and(("note_id", "n-1"))
        stmt = (
            builder.select_from("activity")
            .columns("note_id", "COUNT(*) AS score")
            .where_clause(clause)
            .group_by("note_id")
            .order_by("score DESC")
            .build()
        )
        assert "GROUP BY note_id" in stmt.sql
        assert "ORDER BY score DESC" in stmt.sql
        assert stmt.params == ("n-1",)

    def test_sqlite_group_by_chain(self) -> None:
        builder = SqliteSqlBuilder()
        clause = WhereClause.empty().add_or(("note_id", ["a", "b"]))
        stmt = (
            builder.select_from("activity")
            .columns("note_id", "COUNT(*) AS score")
            .where_clause(clause)
            .group_by("note_id")
            .order_by("score DESC")
            .limit(10)
            .build()
        )
        assert "GROUP BY note_id" in stmt.sql
        assert "LIMIT ?" in stmt.sql
        # List values spread into individual ``?`` placeholders.
        assert stmt.params == ("a", "b", 10)

    def test_no_where_clause_emits_no_where_keyword(self) -> None:
        """Empty clause omits ``WHERE`` entirely."""
        builder = SqliteSqlBuilder()
        stmt = (
            builder.select_from("activity")
            .columns("id")
            .where_clause(WhereClause.empty())
            .limit(5)
            .build()
        )
        assert "WHERE" not in stmt.sql
        assert stmt.params == (5,)


# --------------------------------------------------------------------------
# Cross-dialect param equivalence
# --------------------------------------------------------------------------


class TestCrossDialect:
    """The same logical filter yields the same params on both dialects."""

    @pytest.mark.parametrize(
        "and_pairs, or_pairs",
        [
            (
                (("note_id", "n-1"), ("at >= $N", "2024-01-01")),
                (("actor_id", "alice"), ("actor_id", "bob")),
            ),
            (
                (("action", ["a", "b"]),),
                (),
            ),
            (
                (("actor_id", None), ("role", "writer")),
                (),
            ),
        ],
    )
    def test_params_match(self, and_pairs, or_pairs) -> None:
        clause = WhereClause(and_pairs=and_pairs, or_pairs=or_pairs)
        _, pg_params = render_where(clause, dialect="postgres")
        _, sq_params = render_where(clause, dialect="sqlite")
        assert pg_params == sq_params