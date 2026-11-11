"""Generic rule primitives.

A *rule* is a pure predicate over a context object that raises
:class:`RuleViolation` when the context is not allowed.  A
:class:`RuleEngine` runs a fixed set of rules in order and stops at the
first violation.

Rules never touch a repo, a clock or a client.  The caller (a service)
computes the context -- typically the state a mutation *would* produce
-- and hands it in, so the business invariant stays testable without
any infrastructure and the service body stays thin.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Iterable, Tuple, TypeVar

ContextT = TypeVar("ContextT")


class RuleViolation(ValueError):
    """Raised by a :class:`RuleABC` when a state change is not allowed.

    Subclasses :class:`ValueError` on purpose so the gRPC adapters map
    it to ``INVALID_ARGUMENT`` through their existing value-error
    branch without any rule-specific knowledge.
    """


class RuleABC(ABC, Generic[ContextT]):
    """One business rule over a context.

    Implementations are expected to be pure and deterministic: given
    the same context they either return or raise the same
    :class:`RuleViolation`.

    Note:
        Rules are synchronous on purpose.  A rule that needs data must
        receive it on the context; the caller does the I/O.
    """

    @abstractmethod
    def check(self, context: ContextT) -> None:
        """Validate ``context``.

        Args:
            context: the state change to inspect.

        Raises:
            :exc:`RuleViolation`: the context is not allowed.
        """
        ...


class RuleEngine(Generic[ContextT]):
    """Runs a fixed set of :class:`RuleABC` in insertion order.

    Args:
        rules: the rules to enforce.  Empty by default so a caller can
            opt in to only the rules it needs.
    """

    def __init__(self, rules: Iterable[RuleABC[ContextT]] = ()) -> None:
        self._rules: Tuple[RuleABC[ContextT], ...] = tuple(rules)

    @property
    def rules(self) -> Tuple[RuleABC[ContextT], ...]:
        """The rules this engine enforces, in order."""
        return self._rules

    def enforce(self, context: ContextT) -> None:
        """Run every rule against ``context``.

        Stops at the first rule that raises, so callers see the
        highest-priority violation only.

        Args:
            context: the state change to inspect.

        Raises:
            :exc:`RuleViolation`: the first rule rejected ``context``.
        """
        for rule in self._rules:
            rule.check(context)


__all__ = ["RuleABC", "RuleEngine", "RuleViolation"]
