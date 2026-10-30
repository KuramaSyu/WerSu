from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple
from src.api.repos.combined_note_repo import CombinedNoteRepoABC
from src.api.repos.tag_repo import TagRepoABC, TagSubjectType
from src.api.services.directory_service import DirectoryIncludeOptions
from src.api.services.note_service import NoteIncludeOptions
from src.api.other.undefined import UNDEFINED, is_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db.database import DatabaseABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.tag import TagEntity
from src.db.entities.note.versioning import NoteVersionEntry
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
    DirectoryParentType,
)
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.other.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.repos.note_content_repo import NoteContentRepo
from src.db.table import TableABC
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.api.facades.note_facade import NoteFacadeABC, SearchType
from src.api.services.note_service import NoteResponse, NoteServiceABC
from src.services.attachment_facade import Attachment, AttachmentFacadeABC
from src.db.repos.note.versioning import NoteVersionRepoABC


class _FakeSpyAttachmentFacade(AttachmentFacadeABC):
    """In-memory :class:`AttachmentFacadeABC` for directory-service tests.

    Implements only the operations the directory service calls
    (``get_metadata``, ``delete_attachment``,
    ``link_attachment_to_note``); every other method raises so
    accidental callers fail loud.
    """

    def __init__(self) -> None:
        self.metadata_by_key: Dict[str, Attachment] = {}
        self.deleted: List[str] = []
        self.links: List[tuple[str, str]] = []

    def seed_metadata(self, attachment: Attachment) -> None:
        """Pre-populate metadata for ``get_metadata`` lookups."""
        self.metadata_by_key[str(attachment.key)] = attachment

    async def post_attachment(
        self, attachment: Attachment, user_ctx: UserContextABC
    ) -> Attachment:  # pragma: no cover - unused
        raise NotImplementedError

    async def update_metadata(
        self, attachment: Attachment, user_ctx: UserContextABC
    ) -> Attachment:  # pragma: no cover - unused
        raise NotImplementedError

    async def get_attachment(
        self, key: str, user_ctx: UserContextABC
    ) -> Attachment:  # pragma: no cover - unused
        raise NotImplementedError

    async def get_metadata(
        self, key: str, user_ctx: UserContextABC
    ) -> Attachment:
        return self.metadata_by_key[str(key)]

    async def delete_attachment(self, key: str, user_ctx: UserContextABC) -> None:
        self.deleted.append(str(key))

    async def link_attachment(
        self,
        attachment_key: str,
        sub_type: str,
        sub_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        self.links.append((str(attachment_key), sub_type, str(sub_id)))

    async def unlink_attachment(
        self,
        attachment_key: str,
        sub_type: str,
        sub_id: str,
        user_ctx: UserContextABC,
    ) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    async def list_attachments(
        self, sub_type: str, sub_id: str, user_ctx: UserContextABC
    ) -> List[Attachment]:  # pragma: no cover - unused
        raise NotImplementedError

    async def get_attachment_token(
        self, attachment_key: str, user_ctx: UserContextABC
    ) -> str:  # pragma: no cover - unused
        raise NotImplementedError


__all__ = [
    '_FakeSpyAttachmentFacade',
]
