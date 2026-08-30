"""Bootstrap-strategy protocol shared by the shelf subsystem.

A bootstrap strategy is a named, idempotent recipe that runs
immediately after a shelf row lands.  The user bootstrap path
(:meth:`~src.services.user_service.UserServiceImpl.create_user`)
and the new ``CreateShelf`` gRPC RPC both invoke the same
strategy objects, so the two flows can never diverge.

Strategies are **stateful with respect to their dependencies**
-- ``shelf_repo``, ``rule_repo``, ``directory_facade`` and
``permission_repo`` are bound at construction time, because
they all live for the lifetime of the composition root.  The
``apply`` method only takes the *per-shelf* inputs (``shelf``,
``owner_id``, ``user_ctx``).

Adding a new strategy:

1. Create a module under :mod:`src.services.shelf_bootstrap`
   (e.g. ``reading_list.py``).
2. Subclass :class:`ShelfBootstrapStrategy` (or implement the
   protocol directly).
3. Register a factory in :mod:`src.services.shelf_bootstrap.registry`.
4. Add the matching enum value to
   :class:`~src.api.services.shelf_service.BootstrapStrategy`
   and the corresponding ``BOOTSTRAP_STRATEGY_*`` proto
   variant.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.user_context import UserContextABC
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.repos.rule_repo import RuleRepoABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.api.services.shelf_service import BootstrapResult
from src.db.entities.shelf import ShelfEntity


@runtime_checkable
class ShelfBootstrapStrategy(Protocol):
    """Idempotent recipe that runs after a shelf row lands.

    Implementations must:

    * Be **idempotent** -- re-running on a shelf that already
      carries books is a no-op.  Probe
      :meth:`ShelfRepoABC.get_books_of` first.
    * **Not insert the shelf itself** -- the caller already did.
    * **Not insert permission relations** that the calling code
      owns (admin is derived from owner in the schema;
      :class:`src.services.shelf_service.ShelfServiceImpl`
      inserts ``shelf#owner``).  Strategies own the *content*
      on the shelf, not its access control.
    * Bind their dependencies in ``__init__``; only the
      per-shelf inputs (``shelf``, ``owner_id``, ``user_ctx``)
      flow through :meth:`apply`.
    * Return a populated :class:`BootstrapResult` describing
      what they created, or a sentinel one with ``description``
      set when they short-circuit.
    """

    name: str

    @abstractmethod
    async def apply(
        self,
        *,
        shelf: ShelfEntity,
        owner_id: str,
        user_ctx: UserContextABC,
    ) -> BootstrapResult:
        """Run the strategy.

        Args:
            shelf: the freshly-inserted shelf.  ``shelf.id`` is
                guaranteed set.
            owner_id: id of the user that created the shelf.
                Stored on rules created by the strategy so the
                ``NoteCreated -> add_to_directory`` default
                rule carries a stable creator.
            user_ctx: caller identity.  Forwarded to
                :meth:`DirectoryFacadeABC.create_directory` so
                each book gets the right admin relation.

        Returns:
            BootstrapResult: describes what the strategy produced.
        """
        ...


#: Bundle of dependencies every strategy needs.  Strategies
#: capture this in ``__init__`` and stay free of any other
#: module-level state.
BootstrapDeps = tuple[
    ShelfRepoABC,
    RuleRepoABC,
    DirectoryFacadeABC,
]


__all__ = ["BootstrapDeps", "ShelfBootstrapStrategy"]