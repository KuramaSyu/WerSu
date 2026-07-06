"""Visitor pattern abstractions for the domain entities.

This module wires up a small double-dispatch layer so that an entity
(:class:`AcceptsVisitor`) can route itself to the right handler on an
:class:`EntityVisitor` without the call site having to know the concrete
type.  It exists primarily to let the gRPC layer collapse the if/elif
chain of `to_grpc_*` converters into one
:class:`~src.grpc_mod.converter.grpc_visitor.ConvertToGrpcVisitor` that
dispatches per entity type.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.api.note_service import NoteResponse
    from src.db.entities.activity import ActivityEntity, ActivityScore
    from src.db.entities.directory.directory import DirectoryEntity
    from src.db.entities.note.metadata import NoteEntity
    from src.db.entities.note.sharing import NoteShareEntity
    from src.db.entities.user.user import UserEntity
    from src.db.repos.attachments.attachments import Attachment


class AcceptsVisitor(ABC):
    """Abstract base for entities that can be visited.

    Concrete entities implement :meth:`visit` (aliased as :meth:`convert`)
    to dispatch themselves to the matching `visit_*` method on the
    supplied visitor.  Using `self.visit(visitor)` (rather than the
    canonical `self.accept(visitor)`) keeps the call site short and
    reads as "this entity visits the visitor".
    """

    @abstractmethod
    def visit(self, visitor: EntityVisitor) -> Any:
        """Dispatch `self` to the matching handler on `visitor`.

        Args:
            visitor: An :class:`EntityVisitor` that will receive this
                entity via its `visit_*` method.

        Returns:
            Whatever the visitor's `visit_*` method returns.  Each
            concrete visitor decides the return type.
        """
        raise NotImplementedError

    def convert(self, visitor: EntityVisitor) -> Any:
        """Alias for :meth:`visit`.

        Reads more naturally at gRPC-adapter call sites:
        `note_entity.convert(self._to_grpc)`.
        """
        return self.visit(visitor)


class EntityVisitor(ABC):
    """Abstract visitor over the domain entities.

    Every concrete visitor implements one `visit_*` method per
    :class:`AcceptsVisitor` subclass it supports. The default
    implementations raise :exc:`NotImplementedError` so subclasses
    must opt in to the entities they care about.

    Implementations:
        * :class:`src.grpc_mod.converter.grpc_visitor.ConvertToGrpcVisitor`
        * :class:`tests.stubs.visitor.StubVisitor`
    """

    @abstractmethod
    def visit_note(self, entity: NoteEntity) -> Any:
        """Handle a :class:`~src.db.entities.note.metadata.NoteEntity`.

        Raises:
            NotImplementedError: If the visitor does not support notes.
        """
        raise NotImplementedError

    @abstractmethod
    def visit_note_minimal(self, entity: NoteEntity) -> Any:
        """Handle a :class:`~src.db.entities.note.metadata.NoteEntity` projected to a minimal view.

        Used for search results where the full note payload is not
        needed. Implementations may project the entity differently
        than :meth:`visit_note`.

        Raises:
            NotImplementedError: If the visitor does not support this view.
        """
        raise NotImplementedError

    @abstractmethod
    def visit_directory(self, entity: DirectoryEntity) -> Any:
        """Handle a :class:`~src.db.entities.directory.directory.DirectoryEntity`.

        Raises:
            NotImplementedError: If the visitor does not support directories.
        """
        raise NotImplementedError

    @abstractmethod
    def visit_user(self, entity: UserEntity) -> Any:
        """Handle a :class:`~src.db.entities.user.user.UserEntity`.

        Raises:
            NotImplementedError: If the visitor does not support users.
        """
        raise NotImplementedError

    @abstractmethod
    def visit_note_share(self, entity: NoteShareEntity) -> Any:
        """Handle a :class:`~src.db.entities.note.sharing.NoteShareEntity`.

        Raises:
            NotImplementedError: If the visitor does not support note shares.
        """
        raise NotImplementedError

    @abstractmethod
    def visit_attachment(self, entity: Attachment) -> Any:
        """Handle an :class:`~src.db.repos.attachments.attachments.Attachment`.

        Raises:
            NotImplementedError: If the visitor does not support attachments.
        """
        raise NotImplementedError

    @abstractmethod
    def visit_attachment_metadata(self, entity: Attachment) -> Any:
        """Handle an :class:`~src.db.repos.attachments.attachments.Attachment` projected to metadata only.

        Raises:
            NotImplementedError: If the visitor does not support this view.
        """
        raise NotImplementedError

    @abstractmethod
    def visit_note_response(self, response: NoteResponse) -> Any:
        """Handle a :class:`~src.api.note_service.NoteResponse`.

        Used by the gRPC layer to render the result of
        :meth:`~src.api.note_service.NoteServiceABC.get_note` --
        which pairs a resolved :class:`NoteEntity` with an
        attachment id -> JWT map for temporary users.

        Raises:
            NotImplementedError: If the visitor does not support note responses.
        """
        raise NotImplementedError

    @abstractmethod
    def visit_activity(self, entity: "ActivityEntity") -> Any:
        """Handle an :class:`~src.db.entities.activity.ActivityEntity`.

        Used by the gRPC layer to render individual activity log
        rows.  Implementations map each field to its proto equivalent;
        ``metadata`` is JSON-serialised.

        Raises:
            NotImplementedError: If the visitor does not support activity rows.
        """
        raise NotImplementedError

    @abstractmethod
    def visit_activity_score(self, score: "ActivityScore") -> Any:
        """Handle an :class:`~src.db.entities.activity.ActivityScore`.

        Used by the gRPC layer to render aggregate most-used scores.
        The aggregation collapses many events into one ``(note_id,
        score)`` pair; the visitor renders the score as a float.

        Raises:
            NotImplementedError: If the visitor does not support activity scores.
        """
        raise NotImplementedError