from dataclasses import dataclass
from typing import List

from src.api.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.db.repos.note.permission import Relationship


@dataclass
class DirectoryEntity:
    """Represents one directory with metadata and SpiceDB relations.

    Parameters
    ----------
    id : UndefinedOr[str], default=UNDEFINED
        Directory identifier from Postgres.
    name : UndefinedNoneOr[str], default=UNDEFINED
        Human-readable directory name.
    display_name : UndefinedNoneOr[str], default=UNDEFINED
        Display name for the directory.
    description : UndefinedNoneOr[str], default=UNDEFINED
        Optional description shown for the directory purpose.
    image_url : UndefinedNoneOr[str], default=UNDEFINED
        Optional image URL for the directory.
    parent_id : UndefinedNoneOr[str], default=UNDEFINED
        Optional parent directory ID (stored as SpiceDB `parent` relation).
    relations : UndefinedOr[List[Relationship]], default=UNDEFINED
        User-facing relations such as `admin`, `writer`, and `reader`.
    """

    id: UndefinedOr[str] = UNDEFINED
    name: UndefinedNoneOr[str] = UNDEFINED
    display_name: UndefinedNoneOr[str] = UNDEFINED
    description: UndefinedNoneOr[str] = UNDEFINED
    image_url: UndefinedNoneOr[str] = UNDEFINED
    parent_id: UndefinedNoneOr[str] = UNDEFINED
    relations: UndefinedOr[List[Relationship]] = UNDEFINED
