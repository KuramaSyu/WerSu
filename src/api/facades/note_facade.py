"""Contract for the note DB facade.

The single-interface boundary the service layer talks to.  Concrete
implementations live under :mod:`src.db.repos.note` (e.g.
:class:`src.db.repos.note.note.NoteFacadeImpl`).

Implementations:
* :class:`src.db.repos.note.note.NoteFacadeImpl`
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from src.api.other.types import Pagination
from src.api.other.user_context import UserContextABC
from src.api.services.note_service import NoteIncludeOptions

if TYPE_CHECKING:
    from src.db.entities.note.metadata import NoteEntity


class SearchType(Enum):
    """Supported search strategies for :meth:`NoteRepoFacadeABC.search_notes`.

    Values:
        NO_SEARCH: date-only / unscoped listing.
        FULL_TEXT_TITLE: exact title/content match via Postgres
            ``tsvector``.
        FUZZY: typo-tolerant match across title and content.
        CONTEXT: semantic match using vector embeddings.
    """

    NO_SEARCH = 1
    FULL_TEXT_TITLE = 2
    FUZZY = 3
    CONTEXT = 4


class NoteFacadeABC(ABC):
    """Composite note repository spanning content, embeddings and permission repos.

    Every method is a thin DB wrapper; permission enforcement lives in
    :class:`src.api.note_service.NoteServiceABC`.  Implementations are
    expected to enrich each returned :class:`~src.db.entities.note.metadata.NoteEntity`
    with its parent directories and tags and write them to the `directory_ids` and `tag_ids` fields, respectively.

    Implementations:
    * :class:`src.db.repos.note.note.NoteFacadeImpl`
    """

    @property
    def embedding_table_name(self) -> str:
        """Return the table name backing note embeddings."""
        return "note.embedding"

    @property
    def content_table_name(self) -> str:
        """Return the table name backing note content."""
        return "note.content"

    @property
    def permission_table_name(self) -> str:
        """Return the table name backing note permissions."""
        return "note.permission"

    @abstractmethod
    async def insert(
        self,
        note: "NoteEntity",
        user: UserContextABC,
    ) -> "NoteEntity":
        """Persist a new note and grant owner/parent-directory relations.

        Args:
            note: the note to insert.  `note_id` is ignored - the repo
                assigns one and returns it on the result.
            user: caller identity; becomes the owner relation and
                scopes the parent-directory lookup.

        Returns:
            NoteEntity: the persisted note, with `note_id` and
            `permissions` populated.
        """

    @abstractmethod
    async def update(
        self,
        note: "NoteEntity",
        ctx: UserContextABC,
    ) -> "NoteEntity":
        """Replace mutable fields of an existing note (content, title).

        Args:
            note: replacement content.  Only fields that are not
                :obj:`~src.api.undefined.UNDEFINED` are written.
            ctx: caller identity, currently unused at this layer
                (permission checks happen upstream).

        Returns:
            NoteEntity: the updated note.
        """

    @abstractmethod
    async def delete(
        self,
        note_id: str,
        ctx: UserContextABC,
    ) -> Optional[List["NoteEntity"]]:
        """Delete a note by id.

        Args:
            note_id: id of the note to delete.
            ctx: caller identity.

        Returns:
            Optional[List[NoteEntity]]: deleted notes (length 0 or 1),
            or `None` if nothing matched.
        """

    @abstractmethod
    async def select_by_id(
        self,
        note_id: str,
        ctx: UserContextABC,
        *,
        include: Optional[NoteIncludeOptions] = None,
        include_permissions: bool = True,
    ) -> Optional["NoteEntity"]:
        """Resolve a single note by id, with relations attached.

        Args:
            note_id: id of the note.
            ctx: caller identity.
            include: opt-in enrichment flags; see
                :class:`~src.api.note_service.NoteIncludeOptions`.
                When omitted (or every flag ``False``) only the row
                is fetched and `note.directory_ids` /
                `note.tag_ids` stay at
                :obj:`~src.api.undefined.UNDEFINED`.
            include_permissions: when `False`, skip the per-note
                permission lookup and leave `note.permissions = []`.
                Useful for list / preview endpoints that do not
                render relations.  Defaults to `True`.

        Returns:
            Optional[NoteEntity]: the resolved note, or `None` when
            no note with that id is visible.
        """

    @abstractmethod
    async def select_by_ids(
        self,
        note_ids: List[str],
        ctx: UserContextABC,
        *,
        include: Optional[NoteIncludeOptions] = None,
        include_permissions: bool = True,
    ) -> List["NoteEntity"]:
        """Bulk variant of :meth:`select_by_id`.

        Args:
            note_ids: ids of the notes to resolve.  Order is
                preserved in the result.  Empty input is a
                programming error.
            ctx: caller identity.
            include: opt-in enrichment flags; see
                :class:`~src.api.note_service.NoteIncludeOptions`.
                Defaults to `None` (no enrichment).
            include_permissions: when `False`, skip the per-note
                permission lookup on every hit.  Defaults to `True`.

        Raises:
            ValueError: when `note_ids` is empty or any id cannot be
            resolved.

        Returns:
            List[NoteEntity]: resolved notes in `note_ids` order.
            `directory_ids` / `tag_ids` are populated iff their
            matching `include` flag was set.
        """

    @abstractmethod
    async def search_notes(
        self,
        search_type: SearchType,
        query: str,
        ctx: UserContextABC,
        pagination: Pagination,
    ) -> List["NoteEntity"]:
        """Run the configured search strategy.

        Args:
            search_type: which strategy to use (see
                :class:`SearchType`).
            query: search text.  Interpretation depends on
                `search_type`.
            ctx: caller identity; used to scope the result set.
            pagination: offset/limit window for the search.

        Returns:
            List[NoteEntity]: matching notes in the strategy's
            natural order, at most `pagination.limit` rows.
        """


__all__ = ["NoteRepoFacadeABC", "SearchType"]
