"""Shelf-bootstrap strategies shared by user-create and shelf-create.

Public surface:

* :class:`ShelfBootstrapStrategy` -- the protocol every strategy
  implements (:mod:`strategy`).
* :class:`ZettelkastenStrategy` -- the default zettelkasten
  bootstrap recipe (:mod:`zettelkasten`).
* :data:`STRATEGIES` + :func:`build_strategy` -- the lookup
  registry that produces **bound** strategy instances from a
  dependency bundle (:mod:`registry`).

New strategies register by adding themselves to
:data:`registry.STRATEGIES` and adding the matching enum value
to :class:`~src.api.services.shelf_service.BootstrapStrategy`.
"""

from src.services.shelf_bootstrap.registry import STRATEGIES, build_strategy
from src.services.shelf_bootstrap.strategy import ShelfBootstrapStrategy
from src.services.shelf_bootstrap.zettelkasten import (
    ZettelkastenStrategy,
    ensure_default_fleeting_rule,
)


__all__ = [
    "STRATEGIES",
    "ShelfBootstrapStrategy",
    "ZettelkastenStrategy",
    "build_strategy",
    "ensure_default_fleeting_rule",
]