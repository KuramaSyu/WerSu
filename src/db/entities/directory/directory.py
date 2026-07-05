from dataclasses import dataclass
from typing import List

from src.api.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api import Relationship
from src.api.visitor import AcceptsVisitor, EntityVisitor


@dataclass
class DirectoryEntity(AcceptsVisitor):
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
    readme_note_id : UndefinedNoneOr[str], default=UNDEFINED
        Optional id of the ``README.md`` note pinned to this directory.
        When set, :meth:`src.services.directory.DirectoryService.get_directory`
        and :meth:`~src.services.directory.DirectoryService.get_directories`
        fetch the note and overlay the parsed `image_url` and
        `description` onto the result.
    relations : UndefinedOr[List[Relationship]], default=UNDEFINED
        User-facing relations such as `admin`, `writer`, and `reader`.
    """

    id: UndefinedOr[str] = UNDEFINED
    name: UndefinedNoneOr[str] = UNDEFINED
    display_name: UndefinedNoneOr[str] = UNDEFINED
    description: UndefinedNoneOr[str] = UNDEFINED
    image_url: UndefinedNoneOr[str] = UNDEFINED
    parent_id: UndefinedNoneOr[str] = UNDEFINED
    readme_note_id: UndefinedNoneOr[str] = UNDEFINED
    relations: UndefinedOr[List[Relationship]] = UNDEFINED

    def visit(self, visitor: EntityVisitor):
        """Dispatch this directory to ``visitor.visit_directory``."""
        return visitor.visit_directory(self)
