"""Structured search filter for :meth:`NoteServiceABC.search_notes`."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import List, Optional

from src.api.errors import SearchFilterError


@dataclass
class NoteSearchFilter:
    """Structured search filter applied on top of a search strategy."""

    include_directory_ids: List[str] = field(default_factory=list)
    exclude_directory_ids: List[str] = field(default_factory=list)
    date_from: Optional[_dt.datetime] = None
    date_until: Optional[_dt.datetime] = None
    include_shelf_ids: List[str] = field(default_factory=list)
    exclude_shelf_ids: List[str] = field(default_factory=list)
    include_tag_ids: List[str] = field(default_factory=list)
    exclude_tag_ids: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return ``True`` when no filter dimension is populated."""
        return all(
            (
                not self.include_directory_ids,
                not self.exclude_directory_ids,
                self.date_from is None,
                self.date_until is None,
                not self.include_shelf_ids,
                not self.exclude_shelf_ids,
                not self.include_tag_ids,
                not self.exclude_tag_ids,
            )
        )

    @staticmethod
    def empty() -> "NoteSearchFilter":
        """Return a :class:`NoteSearchFilter` with every field at its default."""
        return NoteSearchFilter()


def validate_search_filter(filter_: NoteSearchFilter) -> None:
    """Enforce include XOR exclude per dimension and ordered date bounds."""
    if filter_.include_directory_ids and filter_.exclude_directory_ids:
        raise SearchFilterError(
            "note search filter: pass either include_directory_ids "
            "or exclude_directory_ids, not both"
        )
    if filter_.include_shelf_ids and filter_.exclude_shelf_ids:
        raise SearchFilterError(
            "note search filter: pass either include_shelf_ids "
            "or exclude_shelf_ids, not both"
        )
    if filter_.include_tag_ids and filter_.exclude_tag_ids:
        raise SearchFilterError(
            "note search filter: pass either include_tag_ids "
            "or exclude_tag_ids, not both"
        )
    if (
        filter_.date_from is not None
        and filter_.date_until is not None
        and filter_.date_from > filter_.date_until
    ):
        raise SearchFilterError(
            "note search filter: date_from is later than date_until"
        )


__all__ = [
    "NoteSearchFilter",
    "SearchFilterError",
    "validate_search_filter",
]

