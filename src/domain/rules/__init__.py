"""Domain rules: pure business invariants the service layer enforces.

Rules live outside the services so services stay thin.  A service
composes the rule set it needs and calls :meth:`RuleEngine.enforce`
with the state a mutation *would* produce; the rules themselves stay
synchronous and side-effect free.
"""

from src.domain.rules.base import RuleABC, RuleEngine, RuleViolation
from src.domain.rules.shelf import (
    BOOK_SHELF_RULE_ENGINE,
    BookMustHaveAtLeastOneShelf,
    BookMustHaveAtLeastOneShelfError,
    BookShelfState,
)

__all__ = [
    "RuleABC",
    "RuleEngine",
    "RuleViolation",
    "BOOK_SHELF_RULE_ENGINE",
    "BookMustHaveAtLeastOneShelf",
    "BookMustHaveAtLeastOneShelfError",
    "BookShelfState",
]
