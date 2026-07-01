"""Stub :class:`EntityVisitor` used across the visitor tests.

`StubVisitor` records every entity it sees into a per-method list so
individual tests can assert on what was dispatched without re-declaring
a recorder class at every call site.  A per-method `record_*` flag
lets a test focus on a single handler.
"""

from __future__ import annotations

from typing import Any, List

from src.api import Relationship
from src.api.relationship import ObjectRef, SubjectRef
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.sharing import NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.api.visitor import EntityVisitor
from src.db.repos.attachments.attachments import Attachment


class StubVisitor(EntityVisitor):
    """Catch-all :class:`EntityVisitor` for tests.

    Each `visit_*` method appends the entity to the matching list and
    returns it unchanged.  Pass `record_*=False` to mute a handler so
    it becomes a pass-through that returns the entity without storing
    it.

    Attributes:
        notes: entities that went through :meth:`visit_note`.
        note_minimals: entities that went through :meth:`visit_note_minimal`.
        directories: entities that went through :meth:`visit_directory`.
        users: entities that went through :meth:`visit_user`.
        note_shares: entities that went through :meth:`visit_note_share`.
        attachments: entities that went through :meth:`visit_attachment`.
        attachment_metadatas: entities that went through
            :meth:`visit_attachment_metadata`.
        share_users: ``(access_as, online_until)`` pairs that went
            through :meth:`visit_share_user`.
    """

    def __init__(
        self,
        *,
        record_note: bool = True,
        record_note_minimal: bool = True,
        record_directory: bool = True,
        record_user: bool = True,
        record_note_share: bool = True,
        record_attachment: bool = True,
        record_attachment_metadata: bool = True,
    ) -> None:
        """Initialize empty record lists and the per-handler `record_*` flags.

        Args:
            record_note: record entities in :meth:`visit_note`.
            record_note_minimal: record entities in :meth:`visit_note_minimal`.
            record_directory: record entities in :meth:`visit_directory`.
            record_user: record entities in :meth:`visit_user`.
            record_note_share: record entities in :meth:`visit_note_share`.
            record_attachment: record entities in :meth:`visit_attachment`.
            record_attachment_metadata: record entities in
                :meth:`visit_attachment_metadata`.
        """
        self.notes: List[NoteEntity] = []
        self.note_minimals: List[NoteEntity] = []
        self.directories: List[DirectoryEntity] = []
        self.users: List[UserEntity] = []
        self.note_shares: List[NoteShareEntity] = []
        self.attachments: List[Attachment] = []
        self.attachment_metadatas: List[Attachment] = []
        self.share_users: List[tuple[str, Any]] = []
        self._record_note = record_note
        self._record_note_minimal = record_note_minimal
        self._record_directory = record_directory
        self._record_user = record_user
        self._record_note_share = record_note_share
        self._record_attachment = record_attachment
        self._record_attachment_metadata = record_attachment_metadata

    def visit_note(self, entity: NoteEntity) -> NoteEntity:
        if self._record_note:
            self.notes.append(entity)
        return entity

    def visit_note_minimal(self, entity: NoteEntity) -> NoteEntity:
        if self._record_note_minimal:
            self.note_minimals.append(entity)
        return entity

    def visit_directory(self, entity: DirectoryEntity) -> DirectoryEntity:
        if self._record_directory:
            self.directories.append(entity)
        return entity

    def visit_user(self, entity: UserEntity) -> UserEntity:
        if self._record_user:
            self.users.append(entity)
        return entity

    def visit_note_share(self, entity: NoteShareEntity) -> NoteShareEntity:
        if self._record_note_share:
            self.note_shares.append(entity)
        return entity

    def visit_attachment(self, entity: Attachment) -> Attachment:
        if self._record_attachment:
            self.attachments.append(entity)
        return entity

    def visit_attachment_metadata(self, entity: Attachment) -> Attachment:
        if self._record_attachment_metadata:
            self.attachment_metadatas.append(entity)
        return entity

    def visit_share_user(self, access_as: str, online_until: Any) -> Any:
        self.share_users.append((access_as, online_until))
        return None


def make_relationship(resource_id: str, subject_id: str) -> Relationship:
    """Build a minimal :class:`Relationship` used in converter tests."""
    return Relationship(
        resource=ObjectRef(object_type="note", object_id=resource_id),
        relation="writer",
        subject=SubjectRef(object_type="user", object_id=subject_id),
    )