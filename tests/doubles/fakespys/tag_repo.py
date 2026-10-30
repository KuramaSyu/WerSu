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


class _FakeSpyTagRepo(TagRepoABC):
    """In-memory :class:`TagRepoABC` used by pure unit tests.

    Stores the tag taxonomy (id -> slug) and the association
    tables (`note.subject_id -> set(tag_ids)` and
    `directory.subject_id -> set(tag_ids)`).  Exists-and-existence
    checks are answered against this in-memory store so the
    production-side validation logic is exercised end-to-end.
    """

    def __init__(self) -> None:
        # id -> slug
        self.tags_by_id: Dict[str, TagEntity] = {}
        # (subject_type, subject_id) -> set(tag_ids)
        self.bindings: Dict[Tuple[str, str], set] = {}
        # Recorded calls for assertions.
        self.assign_calls: List[tuple[str, str, str]] = []
        self.replace_calls: List[tuple[str, List[str], List[str]]] = []
        self.remove_calls: List[tuple[str, str, str]] = []
        self._next_tag_id = 0

    # ---- tag CRUD ------------------------------------------------------

    async def create_tag(
        self,
        slug: str,
        display_name: str,
    ) -> TagEntity:
        if not slug:
            raise ValueError("slug is required")
        if not display_name:
            raise ValueError("display_name is required")
        for existing in self.tags_by_id.values():
            if existing.slug == slug:
                raise ValueError(f"Tag with slug {slug!r} already exists")
        self._next_tag_id += 1
        tag_id = f"tag-{self._next_tag_id}"
        entity = TagEntity(id=tag_id, slug=slug, display_name=display_name)
        self.tags_by_id[tag_id] = entity
        return entity

    async def get_tag_by_id(self, tag_id: str) -> Optional[TagEntity]:
        return self.tags_by_id.get(str(tag_id))

    async def list_tags(self) -> List[TagEntity]:
        return sorted(
            self.tags_by_id.values(), key=lambda t: t.slug or "",
        )

    async def update_tag(
        self,
        tag_id: str,
        *,
        slug: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Optional[TagEntity]:
        existing = self.tags_by_id.get(str(tag_id))
        if not existing:
            return None
        updated = TagEntity(
            id=existing.id,
            slug=str(slug) if slug is not None else existing.slug,
            display_name=(
                str(display_name) if display_name is not None
                else existing.display_name
            ),
        )
        self.tags_by_id[str(tag_id)] = updated
        return updated

    async def delete_tag(self, tag_id: str) -> bool:
        if not tag_id:
            raise ValueError("tag_id is required")
        removed = self.tags_by_id.pop(str(tag_id), None)
        if removed is None:
            return False
        for key in list(self.bindings.keys()):
            self.bindings[key].discard(str(tag_id))
        return True

    # ---- tag associations ---------------------------------------------

    async def list_tags_for(
        self,
        subject_type: TagSubjectType,
        subject_ids: List[str],
    ) -> Dict[str, List[str]]:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_ids:
            raise ValueError("subject_ids is required")
        cleaned = [str(i) for i in subject_ids if i]
        if not cleaned:
            raise ValueError("subject_ids is required")
        return {
            sid: sorted(self.bindings.get((subject_type, sid), set()))
            for sid in cleaned
        }

    async def assign_tag_to(
        self,
        subject_type: TagSubjectType,
        subject_id: str,
        tag_id: str,
    ) -> None:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_id:
            raise ValueError("subject_id is required")
        if not tag_id:
            raise ValueError("tag_id is required")
        if str(tag_id) not in self.tags_by_id:
            raise ValueError(f"Tag {tag_id!r} does not exist")
        # Note: the production repo also verifies the subject
        # exists in `note.content` / `note.directory`; the in-memory
        # fake is the source of truth here, so any non-empty
        # subject_id is accepted.
        self.assign_calls.append((subject_type, str(subject_id), str(tag_id)))
        self.bindings.setdefault(
            (subject_type, str(subject_id)), set(),
        ).add(str(tag_id))

    async def replace_tags_for(
        self,
        subject_type: TagSubjectType,
        subject_ids: List[str],
        tag_ids: List[str],
    ) -> None:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_ids:
            raise ValueError("subject_ids is required")
        cleaned_subject_ids = [str(i) for i in subject_ids if i]
        if not cleaned_subject_ids:
            raise ValueError("subject_ids is required")
        cleaned_tag_ids = [str(t) for t in tag_ids if t]
        for tag_id in cleaned_tag_ids:
            if tag_id not in self.tags_by_id:
                raise ValueError(f"Tag {tag_id!r} does not exist")

        for sid in cleaned_subject_ids:
            current = self.bindings.get((subject_type, sid), set()).copy()
            desired = set(cleaned_tag_ids)
            self.bindings[(subject_type, sid)] = desired
            self.replace_calls.append((subject_type, sid, list(cleaned_tag_ids)))

    async def remove_tag_from(
        self,
        subject_type: TagSubjectType,
        subject_id: str,
        tag_id: str,
    ) -> None:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_id:
            raise ValueError("subject_id is required")
        if not tag_id:
            raise ValueError("tag_id is required")
        self.remove_calls.append((subject_type, str(subject_id), str(tag_id)))
        bucket = self.bindings.get((subject_type, str(subject_id)))
        if bucket is not None:
            bucket.discard(str(tag_id))


__all__ = [
    '_FakeSpyTagRepo',
]
