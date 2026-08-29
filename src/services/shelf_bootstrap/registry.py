"""Strategy registry: mapping from enum value to factory method
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
        )
    ),
}


def build_strategy(
    name: str,
    *,
    shelf_repo=None,
    rule_repo=None,
    directory_facade=None,
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
    )
    return STRATEGIES[name](deps)


__all__ = ["STRATEGIES", "build_strategy"]