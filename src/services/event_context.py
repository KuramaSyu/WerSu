"""Production :class:`EventContext` backed by the note and directory repos.

The dispatcher and rule listeners receive an
:class:`~src.api.events.event_context.EventContext` alongside
each event.  This module is the production implementation: it
fetches note content / title via
:class:`~src.db.repos.note.content.NoteContentRepo` and walks
the directory tree via the directory facade's
:meth:`~src.api.repos.directory_repo.DirectoryHelperMixin.get_parents_of`
helper.  Shelf lookups go through the
:class:`~src.api.repos.shelf_repo.ShelfRepoABC` so rules can
match on the shelf<->book relation too.

The context is intentionally narrow: it exposes *only* the
operations a rule listener is allowed to perform.  Adding a new
fetch helper here is the canonical way to expand what rules can
see -- do not give listeners the directory / note repos
directly.

Implementations:
* :class:`InMemoryEventContext` -- production, talks to the
  real repos.
* :class:`~src.api.events.event_context.NoopEventContext` --
  no-op variant used in tests and when the rules subsystem is
  disabled.
"""

from __future__ import annotations

from typing import List, Optional

from src.api.events.event_context import EventContext
from src.api.repos.directory_repo import DirectoryHelperMixin
from src.api.repos.shelf_repo import ShelfRepoABC
from src.db.repos.note.content import NoteContentRepo


class InMemoryEventContext(EventContext):
    """Event context backed by the live note + directory + shelf repos.

    Args:
        note_content_repo: repo used by :meth:`note_content` and
            :meth:`note_title`.  Required.
        directory_repo: directory repo used by
            :meth:`directory_ancestor_ids`.  Any object implementing
            :class:`~src.api.repos.directory_repo.DirectoryHelperMixin`
            is accepted (the production wiring passes the
            :class:`~src.db.repos.directory.directory.DirectoryFacadeImpl`
            which itself wraps the Postgres / SpiceDB pair).
        shelf_repo: shelf repo used by :meth:`shelf_contains_book`.
            Required.

    All dependencies are eagerly required; there is no point
    constructing this context without a way to fetch data.  The
    no-op :class:`~src.api.events.event_context.NoopEventContext`
    covers the "rules disabled" path.
    """

    def __init__(
        self,
        note_content_repo: NoteContentRepo,
        directory_repo: DirectoryHelperMixin,
        shelf_repo: ShelfRepoABC,
    ) -> None:
        self._note_content_repo = note_content_repo
        self._directory_repo = directory_repo
        self._shelf_repo = shelf_repo


    async def note_content(self, note_id: str) -> Optional[str]:
        """Return the current content of ``note_id``, or ``None``.

        Catches any error from the underlying repo and returns
        ``None`` instead of propagating -- a rule's evaluation
        must never fail just because a note has been deleted
        between the event emission and the listener invocation.
        """
        try:
            entity = await self._note_content_repo.select_by_id(note_id)
        except Exception:  # noqa: BLE001 -- rule path is best-effort
            return None
        return getattr(entity, "content", None)


    async def note_title(self, note_id: str) -> Optional[str]:
        """Return the current title of ``note_id``, or ``None``.

        Same error-isolation rationale as :meth:`note_content`.
        """
        try:
            entity = await self._note_content_repo.select_by_id(note_id)
        except Exception:  # noqa: BLE001 -- rule path is best-effort
            return None
        return getattr(entity, "title", None)


    async def directory_ancestor_ids(self, directory_id: str) -> List[str]:
        """Return ancestor directory ids of ``directory_id``, nearest first.

        Walks the directory tree one level at a time and returns
        the parent chain in order.  The implementation does not
        fetch the ancestor directories themselves -- only their
        ids, which is what the dispatcher needs for the
        "scope ancestor" check.

        Cyclic hierarchies are guarded against by tracking the
        visited set; a cycle returns whatever has been collected
        so far instead of looping forever.

        Errors from the underlying repo are swallowed and an
        empty list is returned; rule evaluation is best-effort
        and must never bubble a database failure out to the bus.
        """
        seen: set[str] = set()
        chain: List[str] = []
        current = directory_id
        try:
            while True:
                parents = await self._directory_repo.get_parents_of(
                    "directory", current, "directory",
                )
                if not parents:
                    break
                # ``get_parents_of`` returns the direct parents;
                # the next iteration digs one level higher.
                # The first parent is treated as the "next"
                # ancestor; the rest (multi-parenting is possible
                # in the data model, though discouraged) are
                # ignored here because rule scope matching follows
                # the primary-parent chain.
                next_id = parents[0]
                if next_id in seen:
                    break
                chain.append(next_id)
                seen.add(next_id)
                current = next_id
        except Exception:  # noqa: BLE001 -- rule path is best-effort
            return []
        return chain


    async def note_parent_directory_id(self, note_id: str) -> Optional[str]:
        """Return the immediate parent directory id of ``note_id``.

        Mirrors :meth:`directory_ancestor_ids` for the note side:
        asks the directory repo for the parent chain of a note
        (using the ``"note"`` hierarchy type) and returns the
        first entry.  Multi-parenting is possible in the data
        model but rare; we follow the primary-parent chain to
        stay consistent with the directory side.

        Errors are swallowed and ``None`` is returned; rule
        dispatch is best-effort and must never bubble a
        database failure out to the bus.
        """
        try:
            parents = await self._directory_repo.get_parents_of(
                "note", note_id, "directory"
            )
        except Exception:  # noqa: BLE001 -- rule path is best-effort
            return None
        if not parents:
            return None
        return str(parents[0])


    async def shelf_contains_book(
        self,
        shelf_id: str,
        book_id: str,
    ) -> bool:
        """Return ``True`` when ``book_id`` sits on ``shelf_id``.

        Asks the shelf repo for the shelf's book set; the repo
        owns the ``note.shelf_book`` table so the answer is the
        source of truth.  Errors from the underlying repo are
        swallowed and treated as "no match" -- rule dispatch
        is best-effort.
        """
        try:
            books = await self._shelf_repo.get_books_of(str(shelf_id))
        except Exception:  # noqa: BLE001 -- rule path is best-effort
            return False
        return str(book_id) in set(books)


__all__ = ["InMemoryEventContext"]
