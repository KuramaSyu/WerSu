from __future__ import annotations

import logging
from typing import Any, Optional


def silent_logger(
    name: str,
    owner: Optional[Any] = None,
    *,
    prefix: Optional[str] = None,
) -> logging.Logger:
    """Return the standard library logger for ``name``.

    The ``prefix`` kwarg matches the production
    :func:`src.utils.logging.logging_provider` signature so test
    wiring is interchangeable with production.
    """
    return logging.getLogger(name)


__all__ = [
    'silent_logger',
]
