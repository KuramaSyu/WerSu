from dataclasses import dataclass
from typing import Any, Dict

from asyncpg import Record

from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr


@dataclass
class TagEntity:
    """Domain model for one row in ``note.tag``.

    Attributes:
        id: server-assigned UUIDv7 tag id.
        slug: unique machine-readable slug.
        display_name: human-readable label.
    """

    id: UndefinedOr[str] = UNDEFINED
    slug: UndefinedNoneOr[str] = UNDEFINED
    display_name: UndefinedNoneOr[str] = UNDEFINED

    @staticmethod
    def from_record(record: Record | Dict[str, Any]) -> "TagEntity":
        return TagEntity(
            id=record.get("id", UNDEFINED),
            slug=record.get("slug", UNDEFINED),
            display_name=record.get("display_name", UNDEFINED),
        )
