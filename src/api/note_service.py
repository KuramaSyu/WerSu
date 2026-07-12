from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, List, Optional, TypedDict

from src.api.user_context import UserContextABC
from src.api.visitor import AcceptsVisitor, EntityVisitor

if TYPE_CHECKING:
    from src.db.entities.note.metadata import NoteEntity


# Sensible default content length if the caller does not pick one.
# Chosen so the typical "NoteApi list" payload is bounded but still
# useful for previews in the editor UI.
_DEFAULT_STRIP_CONTENT_AT: int = 280


class GetNotesOptions(TypedDict, total=False):
    """Optional knobs for :meth:`NoteServiceABC.get_notes` bulk fetches.

    Every key is optional.  The shape is stable so callers can pass
    `options={...}` literals without importing a class, while the
    builder below exists for ergonomic fluent construction.

    Keys:
        include_content: when `False`, omit `content` from the returned
            :class:`~src.db.entities.note.metadata.NoteEntity` objects
            (useful for list endpoints that only render metadata).
            Defaults to `True`.
        strip_content_at: truncate the `content` of every returned
            :class:`~src.db.entities.note.metadata.NoteEntity` to at
            most this many characters.  Ignored when
            `include_content` is `False`.  Defaults to
            `_DEFAULT_STRIP_CONTENT_AT`.
    """

    include_content: bool
    strip_content_at: int


class NoteIncludeOptions(TypedDict, total=False):
    """Per-note enrichment flags for the note read paths.

    Every key defaults to `False`, so callers opt into the extra
    PostgreSQL round-trips explicitly.  Each `True` flag costs one
    dedicated SQL statement (see
    :meth:`NoteRepoFacadeABC.select_by_id`) and lands its result on
    the matching :class:`~src.db.entities.note.metadata.NoteEntity`
    field:

    * `include_directory_ids` -- populates `note.directory_ids` from
      ``note.directory_note`` (``note_id = $1``).  Cheap: a
      single index lookup.
    * `include_tag_ids` -- populates `note.tag_ids` from
      ``note.note_tag JOIN note.tag``.  Cheap: covers the
      ``(note_id, tag_id)`` primary key.
    * `include_permissions` -- keeps the existing per-note SpiceDB
      lookup under the same option so callers don't have to
      special-case it.  Defaults to `True` to match the historic
      behaviour of :meth:`NoteRepoFacadeABC.select_by_id`.

    Note:
        Callers that only want metadata should pass `{}` (or omit
        the kwarg entirely); the basic content/title/author fetch
        path is the cheapest one and never JOINs the side tables.
    """

    include_directory_ids: bool
    include_tag_ids: bool
    include_permissions: bool


def resolve_include_options(
    options: Optional["NoteIncludeOptions"],
) -> "NoteIncludeOptions":
    """Return `options` filled with the default `False` for every flag.

    Args:
        options: caller-supplied options; `None` or empty is fine.

    Returns:
        NoteIncludeOptions: a fresh mapping with every key resolved
        to a `bool`.
    """
    raw = options or NoteIncludeOptions()
    return NoteIncludeOptions(
        include_directory_ids=bool(raw.get("include_directory_ids", False)),
        include_tag_ids=bool(raw.get("include_tag_ids", False)),
        include_permissions=bool(raw.get("include_permissions", True)),
    )


def resolve_options(options: Optional[GetNotesOptions]) -> GetNotesOptions:
    """Resolve `options` (or `None`) into a full options dict.
    """
    if not options:
        options = GetNotesOptions()
    include_content = options.get("include_content", True)
    strip_content_at = options.get("strip_content_at", _DEFAULT_STRIP_CONTENT_AT)
    return GetNotesOptions(
        include_content=bool(include_content),
        strip_content_at=int(strip_content_at),
    )


class GetNotesOptionsBuilder:
    """Fluent builder for :class:`GetNotesOptions`.

    Used wherever a dict literal would be opaque or when callers want
    to spread the options across multiple steps before resolving them.

    Example:
        options = (
            GetNotesOptionsBuilder()
            .include_content(False)
            .strip_content_at(120)
            .build()
        )
    """

    def __init__(self) -> None:
        self._include_content: bool = True
        self._strip_content_at: int = _DEFAULT_STRIP_CONTENT_AT

    def include_content(self, value: bool) -> "GetNotesOptionsBuilder":
        """Set `include_content` on the built options."""
        self._include_content = value
        return self

    def strip_content_at(self, value: int) -> "GetNotesOptionsBuilder":
        """Set `strip_content_at` on the built options.

        Args:
            value: max number of characters to keep from each note's
                `content`.  Values `<= 0` are treated as "do not
                truncate", which is only useful in combination with
                :meth:`include_content` `False` overrides downstream.
        """
        self._strip_content_at = value
        self._include_content = True  # implicit override
        return self

    def build(self) -> GetNotesOptions:
        """Return a fresh :class:`GetNotesOptions` snapshot."""
        return GetNotesOptions(
            include_content=self._include_content,
            strip_content_at=self._strip_content_at,
        )


@dataclass
class NoteResponse(AcceptsVisitor):
    """Result of a :meth:`NoteServiceABC.get_note` call.

    Attributes:
        `note`: the resolved note, or `None` when no note with that id
            is visible to `user_ctx`.
        `id_token_map`: attachment id -> 15-minute JWT.  Only
            populated when `user_ctx` is a temporary user; empty for
            every other caller.
    """

    note: Optional[NoteEntity] = None
    id_token_map: dict[str, str] = field(default_factory=dict)  # type: ignore

    def visit(self, visitor: EntityVisitor) -> Any:
        """Dispatch this response to ``visitor.visit_note_response``"""
        return visitor.visit_note_response(self)


class NoteServiceABC(ABC):
    """Abstract application service for note reads and writes.

    Implementations:
    * :class:`~src.services.note.NoteService`
    """

    @abstractmethod
    async def get_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
        *,
        include: Optional["NoteIncludeOptions"] = None,
    ) -> NoteResponse:
        """Returns a note, its relationships, embeddings and adds
        additional JWTs for temporary users to access attachments.

        Args:
            note_id: id of the note to resolve.
            user_ctx: caller identity used for permission checks and
                to determine whether per-attachment JWTs are minted.
            include: opt-in enrichment flags; see
                :class:`NoteIncludeOptions`.  When omitted, only
                permissions are loaded (legacy behaviour).

        Returns:
            NoteResponse: the resolved note plus a `id_token_map` of
            attachment id -> 15-minute JWT for every attachment the
            caller can read.  `id_token_map` is empty unless
            `user_ctx.is_temporary_user()` is `True`.

        Raises:
            PermissionError: when `user_ctx` cannot read `note_id`.

        Note:
            Only `GetNote` returns a `NoteResponse` with the JWT map;
            every other note RPC returns a plain `NoteEntity`.
        """

    @abstractmethod
    async def insert_note(
        self,
        note: NoteEntity,
        user_ctx: UserContextABC,
    ) -> NoteEntity:
        """Persist a new note and grant the caller ownership.

        Args:
            note: note to insert.  `note_id` is ignored - the repo
                assigns one and returns it on the result.
            user_ctx: caller identity; becomes the owner relation.

        Returns:
            NoteEntity: the persisted note with its assigned `note_id`.
        """

    @abstractmethod
    async def update_note(
        self,
        note: NoteEntity,
        user_ctx: UserContextABC,
    ) -> NoteEntity:
        """Update anything of an existing note. This can replace relationships
        and regenerate embeddings. 

        Args:
            note: replacement content.  Only fields that are not
                :obj:`~src.api.undefined.UNDEFINED` are written.
            user_ctx: caller identity used for the permission check
                that gates the update.

        Raises:
            PermissionError: when `user_ctx` cannot write to `note_id`.

        Returns:
            NoteEntity: the updated note.
        """

    @abstractmethod
    async def delete_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> Optional[NoteEntity]:
        """Delete a note by id.

        Args:
            note_id: id of the note to delete.
            user_ctx: caller identity used for the permission check
                that gates the deletion.

        Raises:
            PermissionError: when `user_ctx` cannot delete `note_id`.

        Returns:
            Optional[NoteEntity]: the deleted note, or `None` when no
            note with that id is visible to `user_ctx`.
        """

    @abstractmethod
    async def search_notes(
        self,
        search_type: str,
        query: str,
        user_ctx: UserContextABC,
        limit: int,
        offset: int,
    ) -> List[NoteEntity]:
        """Run the embedding / fuzzy / date search pipeline.

        Args:
            search_type: name of a
                :class:`~src.api.note_facade.SearchType` member
                (e.g. `"NO_SEARCH"`, `"FULL_TEXT_TITLE"`, `"FUZZY"`,
                `"CONTEXT"`).  String is used so the API layer does
                not depend on the enum at import time.
            query: search text.  Interpretation depends on
                `search_type`.
            user_ctx: caller identity used to scope the results to
                notes the caller can read.
            limit: max number of notes to return.
            offset: number of notes to skip before returning results.

        Raises:
            PermissionError: when `user_ctx` cannot read the search
                results at all (the search is gated on a top-level
                permission).

        Returns:
            List[NoteEntity]: matching notes, at most `limit` items.
        """

    @abstractmethod
    async def get_notes(
        self,
        note_ids: List[str],
        user_ctx: UserContextABC,
        options: Optional[GetNotesOptions] = None,
    ) -> List[NoteEntity]:
        """Resolve a batch of notes by id.

        Args:
            note_ids: ids of the notes to resolve.  Empty input is a
                programming error.
            user_ctx: caller identity used for the permission check
                that gates reading each note.
            options: optional :class:`GetNotesOptions` controlling
                whether `content` is included and how it is
                truncated.  Defaults preserve full content.

        Raises:
            ValueError: when `note_ids` is empty, when any id is
                missing, or when `user_ctx` cannot read one of the
                requested notes.
            TypeError: when `options` is provided but is not a
                mapping.

        Returns:
            List[NoteEntity]: resolved notes in the order they
            appeared in `note_ids`.  Whether `content` is included
            and how it is truncated is governed by `options`; see
            :class:`GetNotesOptions`.
        """



__all__ = [
    "GetNotesOptions",
    "GetNotesOptionsBuilder",
    "NoteIncludeOptions",
    "NoteResponse",
    "NoteServiceABC",
    "resolve_include_options",
]  