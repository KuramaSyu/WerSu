"""Shared, dependency-free types used across the api layer."""

from dataclasses import dataclass
from typing import Optional, Callable
import logging

type LoggingProvider = Callable[[str, Optional[object]], logging.Logger]
"""Factory that returns a configured :class:`logging.Logger`.

The callable takes a logger ``name`` and an optional ``anchor``
object (typically the owning class/instance) and must return a
:class:`logging.Logger`.  This indirection lets tests inject a
silent logger without monkey-patching the stdlib module.
"""

@dataclass
class Pagination:
    """Standard offset/limit window for list endpoints."""

    limit: int
    offset: int