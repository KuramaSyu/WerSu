"""Strategy registry -- name -> factory that builds a bound instance.

Strategies hold their long-lived dependencies (``shelf_repo``,
``rule_repo``, ``directory_facade``, ``permission_repo``) on the
instance, so the registry cannot simply hand out a singleton.
Each entry in :data:`STRATEGIES` is therefore a **factory** that
takes a :class:`~src.services.shelf_bootstrap.strategy.BootstrapDeps`
tuple and returns a freshly-constructed strategy with those
deps bound.

Lookups are keyed by
:class:`~src.api.services.shelf_service.BootstrapStrategy` enum
value (``"none"`` / ``"zettelkasten"``).  ``"none"`` is
intentionally **not** registered -- the caller short-circuits
on that value before consulting the registry.

Usage from a service or composition root::

    from src.services.shelf_bootstrap import build_strategy
    strategy = build_strategy(
        "zettelkasten",
        shelf_repo=shelf_repo,
        rule_repo=rule_repo,
        directory_facade=directory_facade,
        permission_repo=permission_repo,
    )
    await strategy.apply(shelf=shelf, owner_id=uid, user_ctx=ctx)

Adding a new strategy:

1. Create a module under :mod:`src.services.shelf_bootstrap`
   (e.g. ``reading_list.py``).
2. Subclass :class:`ShelfBootstrapStrategy` (or implement the
   protocol directly) with the same ``__init__`` shape as
   :class:`ZettelkastenStrategy`.
3. Add a factory to :data:`STRATEGIES` below.
4. Add the matching enum value to
   :class:`~src.api.services.shelf_service.BootstrapStrategy`
   and the corresponding ``BOOTSTRAP_STRATEGY_*`` proto
   variant.

No other wiring changes are needed.
"""

from __future__ import annotations

from typing import Callable

from src.services.shelf_bootstrap.strategy import (
    BootstrapDeps,
    ShelfBootstrapStrategy,
)
from src.services.shelf_bootstrap.zettelkasten import ZettelkastenStrategy


#: All registered strategies, keyed by their
#: ``BootstrapStrategy.value``.  Each value is a factory that
#: accepts the long-lived dependencies and returns a bound
#: strategy instance.
STRATEGIES: dict[str, Callable[[BootstrapDeps], ShelfBootstrapStrategy]] = {
    "zettelkasten": (
        lambda deps: ZettelkastenStrategy(
            shelf_repo=deps[0],
            rule_repo=deps[1],
            directory_facade=deps[2],
            permission_repo=deps[3],
        )
    ),
}


def build_strategy(
    name: str,
    *,
    shelf_repo=None,
    rule_repo=None,
    directory_facade=None,
    permission_repo=None,
) -> ShelfBootstrapStrategy:
    """Build a strategy with its dependencies bound.

    Convenience wrapper around :data:`STRATEGIES` for the common
    keyword-argument style.

    Args:
        name: the bootstrap strategy's stable enum value
            (e.g. ``"zettelkasten"``).
        shelf_repo: :class:`ShelfRepoABC` instance.
        rule_repo: :class:`RuleRepoABC` instance.
        directory_facade: :class:`DirectoryFacadeABC` instance.
        permission_repo: :class:`PermissionRepoABC` instance.

    Returns:
        ShelfBootstrapStrategy: the bound instance.

    Raises:
        KeyError: no strategy is registered under ``name``.
            Callers should validate the name upstream
            (the gRPC adapter's :class:`BootstrapStrategy`
            conversion maps unknown proto values to
            :data:`BootstrapStrategy.NONE`).
    """
    deps: BootstrapDeps = (
        shelf_repo,
        rule_repo,
        directory_facade,
        permission_repo,
    )
    return STRATEGIES[name](deps)


__all__ = ["STRATEGIES", "build_strategy"]