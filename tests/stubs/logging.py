"""Default logging provider used in unit tests.

Returns a real :class:`logging.Logger` so any code path that calls
``.debug``/``.info``/``.error`` works the same way it would in
production wiring.
"""

from __future__ import annotations

import logging
from typing import Any, Optional


def silent_logger(name: str, owner: Optional[Any] = None) -> logging.Logger:
    """Return the standard library logger for ``name``."""
    return logging.getLogger(name)


__all__ = ["silent_logger"]