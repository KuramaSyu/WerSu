from dataclasses import dataclass
from typing import List

from src.api.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api import Relationship
from src.api.visitor import AcceptsVisitor, EntityVisitor


@dataclass
class DirectoryEntity(AcceptsVisitor):
    """Represents one directory with metadata and SpiceDB relations.

    Attributes:
        id: directory identifier from Postgres.
        slug: machine-readable directory slug.  Mirrors the
            `note.directory.slug` column.
        display_name: display name for the directory.
        description: optional description shown for the directory
            purpose.
        image_url: optional image URL for the directory.
        readme_note_id: optional id of the `README.md` note pinned
            to this directory.  When set,
            :meth:`src.services.directory.DirectoryService.get_directory`
            and :meth:`~src.services.directory.DirectoryService.get_directories`
            fetch the note and overlay the parsed `image_url` and
            `description` onto the result.
        parent_directory_ids: every directory this one is parented
            under.  Sourced from `note.directory_hierarchy`; empty
            when the directory is a root.
        child_directory_ids: direct child directories contained in
            this directory.  Sourced from
            `note.directory_hierarchy`; empty when none.
        child_note_ids: direct child notes contained in this
            directory.  Sourced from `note.directory_hierarchy`;
            empty when none.  To get the direct child directory
            / note counts, callers derive them as
            `len(child_directory_ids)` /
            `len(child_note_ids)` once those lists are populated.
        tag_ids: tag ids this directory carries, sourced from
            `note.directory_tag`.  Empty list when none.
        relations: user-facing relations such as `admin`, `writer`,
            and `reader`.  Deprecated; kept for internal use only.
    """

    id: UndefinedOr[str] = UNDEFINED
    slug: UndefinedNoneOr[str] = UNDEFINED
    display_name: UndefinedNoneOr[str] = UNDEFINED
    description: UndefinedNoneOr[str] = UNDEFINED
    image_url: UndefinedNoneOr[str] = UNDEFINED
    readme_note_id: UndefinedNoneOr[str] = UNDEFINED
    parent_directory_ids: UndefinedOr[List[str]] = UNDEFINED
    child_directory_ids: UndefinedOr[List[str]] = UNDEFINED
    child_note_ids: UndefinedOr[List[str]] = UNDEFINED
    tag_ids: UndefinedOr[List[str]] = UNDEFINED
    # deprecated -- dont use this anymore. Only for internal usage
    relations: UndefinedOr[List[Relationship]] = UNDEFINED


    def visit(self, visitor: EntityVisitor):
        """Dispatch this directory to `visitor.visit_directory`.

        Args:
            visitor: the :class:`~src.api.visitor.EntityVisitor` to
                route this entity to.

        Returns:
            Whatever :meth:`visitor.visit_directory` returns for
            this entity.
        """
        return visitor.visit_directory(self)
