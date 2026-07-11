"""Shared, dependency-free types used across the api layer."""

from dataclasses import dataclass
from typing import Optional, Protocol
import logging


class LoggingProvider(Protocol):
    """Factory that returns a configured :class:`logging.Logger`.

    The callable takes a logger ``name`` and an optional ``anchor``
    object (typically the owning class/instance) and must return a
    :class:`logging.Logger`.  This indirection lets tests inject a
    silent logger without monkey-patching the stdlib module.

    A protocol is used here, even though its actually a callable. This is, to 
    allow a signature with kwargs (especially `` `prefix` ``). 
    """

    def __call__(
        self,
        file_or_class: str,
        cls_instance: Optional[object] = None,
        *,
        prefix: Optional[str] = None,
    ) -> logging.Logger:
        """Return a logger for `file_or_class`, optionally tagged with `prefix`.

        Args:
            file_or_class: logger name, typically the module `__name__` or a class name.
            cls_instance: anchor whose class qualname is appended to
                the logger name (usually `self`).
            prefix: optional short tag wrapped in `[ ]` and
                prepended to the logger name (e.g. `"sharing facade"`).
        """
        ...


@dataclass
class Pagination:
    """Standard offset/limit window for list endpoints."""

    limit: int
    offset: int
