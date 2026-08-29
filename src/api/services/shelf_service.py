from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, TypedDict

from src.api.other.user_context import UserContextABC
from src.db.entities.shelf import ShelfEntity


# ===== errors ==============================================================


class ShelfServiceError(RuntimeError):
    """Base class for every error raised by the shelf service."""


class ShelfPermissionError(ShelfServiceError, PermissionError):
    """Raised when the caller is not allowed to perform the operation."""


# ===== value objects =======================================================


@dataclass(frozen=True)
class DryDeleteResult:
    """Outcome of a ``delete_shelf(dry=True)`` call.

    Attributes:
        affected_book_ids: every book id that would be detached
            from the shelf if the delete ran for real.
        binding_count: number of ``note.shelf_book`` rows that
            would be removed.  ``len(affected_book_ids)`` and the
            cascade contract guarantee these are equal for
            shelves; the count is provided separately so callers
            don't have to compute it.
    """

    affected_book_ids: List[str] = field(default_factory=list)
    binding_count: int = 0


class BootstrapStrategy(str, Enum):
    """Bootstrap strategies that may run after a shelf is created.

    Values are stable strings so they round-trip cleanly through
    the proto enum (``BOOTSTRAP_STRATEGY_*``) and through the
    registry lookup (``STRATEGIES[bootstrap.value]``).

    Members:
        NONE: no strategy runs; the caller just gets the bare
            shelf back.
        ZETTELKASTEN: creates the three default books
            (``fleeting_notes``, ``literature_notes``,
            ``permanent_notes``), binds them to the shelf, and
            inserts the ``NoteCreated -> add_to_directory(fleeting)``
            rule attached to the new shelf.  Idempotent.
    """

    NONE = "none"
    ZETTELKASTEN = "zettelkasten"


@dataclass(frozen=True)
class BootstrapResult:
    """What a :class:`BootstrapStrategy` produced.

    Empty / zero when no strategy ran.  ``created_directory_ids``
    is empty for ``BootstrapStrategy.NONE``; the other fields
    follow.

    Attributes:
        created_directory_ids: book ids the strategy inserted.
        created_rule_id: id of the rule the strategy inserted,
            or ``None`` when no rule was created.
        description: human-readable summary.  Surfaced on the
            proto ``BootstrapResult`` so the client can show
            "Created 3 books and a default rule" without
            re-deriving it.
    """

    created_directory_ids: List[str] = field(default_factory=list)
    created_rule_id: Optional[str] = None
    description: str = ""


# ===== service ABC =========================================================


class ShelfReadOptions(TypedDict, total=False):
    """Per-call enrichment flags for the shelf read paths.

    Every key defaults to ``False``.  Each ``True`` flag costs
    exactly one extra SQL statement (see
    :meth:`~src.api.repos.shelf_repo.ShelfRepoABC.fetch_shelf`)
    and lands its result on the matching
    :class:`~src.db.entities.shelf.ShelfEntity` field.

    Attributes:
        include_books: populates :attr:`ShelfEntity.book_ids`
            via ``note.shelf_book`` for the loaded shelves.
            Affects ``get_shelf``, ``get_shelves`` and
            ``list_shelves``; ignored by the binding read
            helpers (which already return just the id list).
    """

    include_books: bool


def resolve_shelf_read_options(
    options: Optional["ShelfReadOptions"],
) -> "ShelfReadOptions":
    """Return ``options`` filled with ``False`` for every flag by default."""
    raw = options or ShelfReadOptions()
    return ShelfReadOptions(
        include_books=bool(raw.get("include_books", False)),
    )


class ShelfServiceABC(ABC):
    """Application service for shelf CRUD with permission gating.

    * CRUD for :class:`~src.db.entities.shelf.ShelfEntity` rows.
    * Permission gating on every read / write -- reads need
      ``shelf#view``, writes need ``shelf#write``, deletes need
      ``shelf#delete``.  Creation has no chain check; the service
      inserts ``shelf#owner@user:<actor>`` so the creator always
      has full rights.
    * Book-binding management -- add / remove / replace the
      ``shelf <-> book`` edges.  Every binding write is gated on
      ``shelf#write``.
    * Optional **bootstrap strategy** dispatch on create -- the
      service can run a named strategy (e.g. ``zettelkasten``)
      immediately after the shelf row lands.  Strategies are
      shared with the user bootstrap path so the two flows can
      never diverge.
    * Dry-delete support -- :meth:`delete_shelf` accepts a
      ``dry=True`` flag and returns the would-be-cascade without
      touching the row.  Used by the gRPC layer for confirmation
      UIs.

    The service is intentionally thin: it does not run
    :class:`BootstrapStrategy` implementations, it only invokes
    the ones registered in
    :mod:`src.services.shelf_bootstrap.registry`.  Strategies are
    expected to be idempotent so the migration bootstrap, the
    user-create bootstrap and the new ``CreateShelf`` RPC all
    share one well-tested code path.

    Implementations:
    * :class:`src.services.shelf_service.ShelfServiceImpl`
    """

    # ---- CRUD ------------------------------------------------------------

    @abstractmethod
    async def create_shelf(
        self,
        entity: ShelfEntity,
        actor: UserContextABC,
        *,
        bootstrap: BootstrapStrategy = BootstrapStrategy.NONE,
    ) -> tuple[ShelfEntity, BootstrapResult]:
        """Insert a shelf row + insert ``shelf#owner@user:<actor>``.

        Args:
            entity: shelf payload.  ``id`` is ignored (the repo
                mints a fresh UUID).
            actor: caller identity.  Becomes the shelf's owner.
            bootstrap: optional strategy to run after the row
                lands.  See :class:`BootstrapStrategy`.

        Returns:
            A ``(shelf, bootstrap_result)`` tuple.  When
            ``bootstrap == NONE`` the result is the empty
            :class:`BootstrapResult`.

        Raises:
            ShelfPermissionError: never (creation is unconditional;
                the inserted owner relation authorises the
                creator for every downstream operation).
            ValueError: the payload is malformed (e.g. empty slug).
        """
        ...

    @abstractmethod
    async def get_shelf(
        self,
        shelf_id: str,
        actor: UserContextABC,
        *,
        options: Optional[ShelfReadOptions] = None,
    ) -> Optional[ShelfEntity]:
        """Return one shelf the caller can view.

        Args:
            shelf_id: id of the shelf to load.
            actor: caller identity.
            options: opt-in enrichment flags; see
                :class:`ShelfReadOptions`.  ``include_books=True``
                populates :attr:`ShelfEntity.book_ids`.

        Returns:
            Optional[ShelfEntity]: the shelf, or ``None`` when no
            row matches ``shelf_id``.

        Raises:
            ShelfPermissionError: the caller cannot ``view`` the
                shelf.
        """
        ...

    @abstractmethod
    async def get_shelves(
        self,
        ids: List[str],
        actor: UserContextABC,
        *,
        options: Optional[ShelfReadOptions] = None,
    ) -> List[ShelfEntity]:
        """Return multiple shelves by id, filtered by view-permission.

        Shelves the caller cannot view are silently dropped from
        the result.  Empty input returns ``[]``.

        Args:
            ids: shelf ids to load (one query).
            actor: caller identity.
            options: opt-in enrichment flags; see
                :class:`ShelfReadOptions`.  ``include_books=True``
                populates each entity's :attr:`book_ids`.

        Returns:
            List[ShelfEntity]: matching shelves the caller can
            view, in input order.
        """
        ...

    @abstractmethod
    async def list_shelves(
        self,
        actor: UserContextABC,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        options: Optional[ShelfReadOptions] = None,
    ) -> List[ShelfEntity]:
        """Return every shelf the caller can view, paginated.

        The repo's :meth:`list_user_shelf_ids` returns the
        candidate set, filtered through the permission chain.
        ``limit`` / ``offset`` are applied **after** the
        permission filter so a paginated page cannot leak ids the
        caller can't see.

        Args:
            actor: caller identity.
            limit: optional page size.
            offset: optional page offset.
            options: opt-in enrichment flags; see
                :class:`ShelfReadOptions`.  ``include_books=True``
                populates each entity's :attr:`book_ids`.

        Returns:
            List[ShelfEntity]: visible shelves, paginated.

        Raises:
            ValueError: ``limit`` or ``offset`` is negative.
        """
        ...

    @abstractmethod
    async def update_shelf(
        self,
        entity: ShelfEntity,
        actor: UserContextABC,
    ) -> ShelfEntity:
        """Partially update an existing shelf.

        Field-level semantics match
        :meth:`ShelfRepoABC.update_shelf`: ``UNDEFINED`` leaves
        a column alone, ``None`` clears it, a concrete value
        overwrites it.

        Args:
            entity: the shelf to update.  ``entity.id`` is
                required.
            actor: caller identity.

        Returns:
            ShelfEntity: the post-update shelf (without
            ``book_ids`` populated -- call :meth:`get_shelf` if
            the binding list is needed).

        Raises:
            ShelfPermissionError: the caller cannot ``write`` the
                shelf.
            ValueError: ``entity.id`` is missing, or the payload
                is otherwise malformed.
        """
        ...

    @abstractmethod
    async def delete_shelf(
        self,
        shelf_id: str,
        actor: UserContextABC,
        *,
        dry: bool = False,
    ) -> Optional[DryDeleteResult]:
        """Delete a shelf, optionally dry-run.

        Args:
            shelf_id: id of the shelf to delete.
            actor: caller identity.
            dry: when ``True`` return the would-be-cascade
                without touching the row.

        Returns:
            Optional[DryDeleteResult]: ``None`` when the delete
            ran for real (``dry=False`` and the row was removed),
            otherwise the cascade description.  Note: a real
            delete that removed **zero** rows also returns
            ``None`` -- callers that need "deleted vs missing"
            disambiguation should probe with :meth:`get_shelf`
            first.

        Raises:
            ShelfPermissionError: the caller cannot ``delete`` the
                shelf.
            ValueError: ``shelf_id`` is missing.
        """
        ...

    # ---- book bindings ---------------------------------------------------

    @abstractmethod
    async def set_books(
        self,
        shelf_id: str,
        book_ids: List[str],
        actor: UserContextABC,
    ) -> None:
        """Replace the full book set of ``shelf_id``.

        Args:
            shelf_id: id of the shelf to mutate.
            book_ids: complete list of book ids that should sit
                on the shelf after this call.  Empty list clears
                every binding.  Idempotent.
            actor: caller identity.

        Raises:
            ShelfPermissionError: the caller cannot ``write`` the
                shelf.
            ValueError: ``shelf_id`` is missing.
        """
        ...

    @abstractmethod
    async def attach_book(
        self,
        shelf_id: str,
        book_id: str,
        actor: UserContextABC,
    ) -> None:
        """Idempotently bind ``book_id`` to ``shelf_id``.

        A no-op when the binding exists.

        Args:
            shelf_id: id of the shelf to mutate.
            book_id: id of the book to attach.
            actor: caller identity.

        Raises:
            ShelfPermissionError: the caller cannot ``write`` the
                shelf.
            ValueError: ``shelf_id`` or ``book_id`` is missing.
        """
        ...

    @abstractmethod
    async def detach_book(
        self,
        shelf_id: str,
        book_id: str,
        actor: UserContextABC,
    ) -> None:
        """Remove the ``book_id`` binding from ``shelf_id``.

        A no-op when the binding does not exist.

        Args:
            shelf_id: id of the shelf to mutate.
            book_id: id of the book to detach.
            actor: caller identity.

        Raises:
            ShelfPermissionError: the caller cannot ``write`` the
                shelf.
            ValueError: ``shelf_id`` or ``book_id`` is missing.
        """
        ...

    @abstractmethod
    async def get_books_of_shelf(
        self,
        shelf_id: str,
        actor: UserContextABC,
    ) -> List[str]:
        """Return the book ids sitting on ``shelf_id``.

        Args:
            shelf_id: id of the shelf to inspect.
            actor: caller identity.

        Returns:
            List[str]: book ids, sorted; ``[]`` when the shelf
            has no books.

        Raises:
            ShelfPermissionError: the caller cannot ``view`` the
                shelf.
            ValueError: ``shelf_id`` is missing.
        """
        ...

    @abstractmethod
    async def get_shelves_of_book(
        self,
        book_id: str,
        actor: UserContextABC,
    ) -> List[str]:
        """Return the shelf ids that contain ``book_id``.

        Args:
            book_id: id of the book to inspect.
            actor: caller identity.

        Returns:
            List[str]: shelf ids, sorted; ``[]`` when the book
            sits on no shelf.

        Raises:
            ShelfPermissionError: the caller cannot ``view`` at
                least one of the candidate shelves (the result
                is filtered down to the ones they can see).
            ValueError: ``book_id`` is missing.
        """
        ...


__all__ = [
    "BootstrapResult",
    "BootstrapStrategy",
    "DryDeleteResult",
    "ShelfPermissionError",
    "ShelfReadOptions",
    "ShelfServiceABC",
    "ShelfServiceError",
    "resolve_shelf_read_options",
]