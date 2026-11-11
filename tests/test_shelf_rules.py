"""Unit tests for the generic rule engine and the book/shelf rules in
:mod:`src.domain.rules`.

The engine is pure and synchronous, so these tests need no doubles.
"""

from __future__ import annotations

import pytest

from src.domain.rules import (
    BOOK_SHELF_RULE_ENGINE,
    BookMustHaveAtLeastOneShelfError,
    BookShelfState,
    RuleABC,
    RuleEngine,
    RuleViolation,
)


def test_rule_engine_runs_rules_in_order() -> None:
    log: list[str] = []

    class _Recording(RuleABC[dict]):
        def __init__(self, name: str) -> None:
            self._name = name

        def check(self, context: dict) -> None:
            log.append(self._name)

    engine = RuleEngine((_Recording("a"), _Recording("b")))

    engine.enforce({})

    assert log == ["a", "b"]


def test_rule_engine_stops_at_first_violation() -> None:
    log: list[str] = []

    class _Boom(RuleABC[dict]):
        def check(self, context: dict) -> None:
            raise RuleViolation("boom")

    class _Recording(RuleABC[dict]):
        def check(self, context: dict) -> None:
            log.append("after")

    engine = RuleEngine((_Boom(), _Recording()))

    with pytest.raises(RuleViolation):
        engine.enforce({})

    assert log == []


def test_book_must_keep_at_least_one_shelf() -> None:
    with pytest.raises(BookMustHaveAtLeastOneShelfError):
        BOOK_SHELF_RULE_ENGINE.enforce(BookShelfState(book_id="book-1"))


def test_book_with_a_shelf_passes() -> None:
    BOOK_SHELF_RULE_ENGINE.enforce(
        BookShelfState(book_id="book-1", shelf_ids=("shelf-1",))
    )


def test_rule_violation_is_a_value_error() -> None:
    # The gRPC adapters map ValueError -> INVALID_ARGUMENT, so a rule
    # violation surfaces as an invalid-argument status.
    assert issubclass(BookMustHaveAtLeastOneShelfError, ValueError)
