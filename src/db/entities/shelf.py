"""Domain entity for a shelf.

A shelf is a flat (non-hierarchical) grouping of books
(directories).  The shape deliberately mirrors
:class:`~src.db.entities.directory.directory.DirectoryEntity` so
the directory service / facade can render shelves in the same
"container with metadata + README pointer" shape, but a shelf
itself has no parents, no children, and no tags -- only the list
of books sitting on it.

The entity is a :class:`AcceptsVisitor` so the gRPC visitor can
route it to ``visit_shelf`` alongside the other domain entities.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api.other.visitor import AcceptsVisitor


@dataclass
class ShelfEntity(AcceptsVisitor):
    """One row of ``note.shelf``.

    Ownership lives in SpiceDB (``shelf#owner@user:<id>``);
    Postgres only stores the row + its metadata + the
    ``note.shelf_book`` bindings.

    Attributes:
        id: server-generated UUID primary key.
        slug: machine-readable shelf slug.  Mirrors the
            ``note.shelf.slug`` column.
        display_name: optional display name for the shelf.
        description: optional description shown for the shelf
            purpose.
        image_url: optional image URL for the shelf.
        readme_note_id: optional id of the ``README.md`` note
            pinned to this shelf.  Mirrors the same column on
            ``note.directory`` so the directory service's
            README-overlay behaviour applies to shelves with no
            extra wiring.
        book_ids: ids of every book (directory) sitting on this
            shelf, sourced from ``note.shelf_book``.  Empty when
            the shelf is empty.
    """

    id: UndefinedOr[str] = UNDEFINED
    slug: UndefinedNoneOr[str] = UNDEFINED
    display_name: UndefinedNoneOr[str] = UNDEFINED
    description: UndefinedNoneOr[str] = UNDEFINED
    image_url: UndefinedNoneOr[str] = UNDEFINED
    readme_note_id: UndefinedNoneOr[str] = UNDEFINED
    book_ids: UndefinedOr[list[str]] = UNDEFINED

    def visit(self, visitor):
        """Dispatch this shelf to ``visitor.visit_shelf``."""
        return visitor.visit_shelf(self)