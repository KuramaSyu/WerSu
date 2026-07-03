from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

from src.api.user_context import UserContextABC

if TYPE_CHECKING:
    from src.db.entities.note.metadata import NoteEntity


@dataclass
class NoteResponse:
    """Result of a :meth:`NoteServiceABC.get_note` call.

    Attributes:
        `note`: the resolved note, or `None` when no note with that id
            is visible to `user_ctx`.
        `id_token_map`: attachment id -> 15-minute JWT.  Only
            populated when `user_ctx` is a temporary user; empty for
            every other caller.
    """

    note: Optional[NoteEntity] = None
    id_token_map: dict[str, str] = field(default_factory=dict)


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
    ) -> NoteResponse:
        """Returns a note, it's relationships, embeddings and adds 
        additional JWTs for temporary users to access attachments.

        Args:
            note_id: id of the note to resolve.
            user_ctx: caller identity used for permission checks and
                to determine whether per-attachment JWTs are minted.

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
                :class:`~src.db.repos.note.note.SearchType` member
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


__all__ = ["NoteResponse", "NoteServiceABC"]