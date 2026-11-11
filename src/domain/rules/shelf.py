"""Book <-> shelf rules.

A shelf is a flat grouping of books (directories).  A book may sit on
many shelves, but a book must always sit on at least one -- otherwise
it is unreachable from the shelf UI.  The rules here encode that
invariant so the services only have to build the context (the shelf
set a mutation *would* leave the book with) and call the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from src.domain.rules.base import RuleABC, RuleEngine, RuleViolation


@dataclass(frozen=True)
class BookShelfState:
    """The shelf set a book *would* have after a mutation.

    Attributes:
        book_id: the book (directory) the mutation targets.
        shelf_ids: the shelves the book would still sit on.  The
            caller derives this from the requested change -- e.g. an
            empty patch list, or the current shelf set minus the
            shelf the book is being detached from.
    """

    book_id: str
    shelf_ids: Tuple[str, ...] = ()


class BookMustHaveAtLeastOneShelfError(RuleViolation):
    """Raised when a mutation would leave a book without any shelf."""

    def __init__(self, book_id: str) -> None:
        super().__init__(f"book {book_id!r} must belong to at least one shelf")
        self.book_id = book_id


class BookMustHaveAtLeastOneShelf(RuleABC[BookShelfState]):
    """A book must never end up without at least one shelf.

    This blocks every "remove the last shelf from a book" mutation:
    patching a directory's ``shelf_ids`` to an empty list, detaching
    the book from its only shelf, and replacing a shelf's book set in
    a way that orphans one of the removed books.
    """

    def check(self, context: BookShelfState) -> None:
        """Raise when ``context.shelf_ids`` is empty."""
        if not context.shelf_ids:
            raise BookMustHaveAtLeastOneShelfError(context.book_id)


#: Default rule set for the shelf / directory services.
BOOK_SHELF_RULE_ENGINE: RuleEngine[BookShelfState] = RuleEngine(
    (BookMustHaveAtLeastOneShelf(),)
)


__all__ = [
    "BOOK_SHELF_RULE_ENGINE",
    "BookMustHaveAtLeastOneShelf",
    "BookMustHaveAtLeastOneShelfError",
    "BookShelfState",
]
