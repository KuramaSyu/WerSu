"""In-memory :class:`DirectoryServiceABC` stub for unit tests.

The stub records every call so test assertions can target a specific
caller (and its arguments) without having to re-implement the whole
:class:`~src.api.directory_service.DirectoryServiceABC` in every test
file.  Per-method `*_deny` flags force the stub to raise
``PermissionError`` so the gRPC adapter's permission-denied branches
remain testable.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from src.api.services.directory_service import DirectoryServiceABC
from src.api.repos.permission_repo import DirectoryChild
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity


class _StubDirectoryService(DirectoryServiceABC):
    """In-memory :class:`DirectoryServiceABC` used by service and adapter tests."""

    def __init__(self) -> None:
        self.last_get_directory_user_id: Optional[str] = None
        self.last_get_directories_user_id: Optional[str] = None
        self.last_get_notes_args: Optional[tuple] = None
        self.get_directories_parent_id: Optional[str] = None
        self.get_directories_limit: Optional[int] = None
        self.get_directories_offset: Optional[int] = None

        self.directories_by_id: Dict[str, DirectoryEntity] = {}
        self.next_directory_id = 0
        self.directories_for_user: Dict[str, List[DirectoryEntity]] = {}

        self.last_create_entity: Optional[DirectoryEntity] = None
        self.last_create_user_id: Optional[str] = None
        self.last_patch_entity: Optional[DirectoryEntity] = None
        self.last_patch_user_id: Optional[str] = None
        self.last_delete_id: Optional[str] = None
        self.last_delete_user_id: Optional[str] = None
        self.delete_result: bool = True
        self.patch_result: Optional[DirectoryEntity] = None

        self.get_directory_deny: bool = False
        self.get_directories_deny: bool = False
        self.get_notes_deny: bool = False
        self.create_deny: bool = False
        self.patch_deny: bool = False
        self.delete_deny: bool = False

        self.notes_for_directory: Dict[str, List[NoteEntity]] = {}

    async def get_directory_notes(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
        limit: int,
        offset: int,
    ) -> List[NoteEntity]:
        self.last_get_notes_args = (directory_id, user_ctx.user_id, limit, offset)
        if self.get_notes_deny:
            raise PermissionError("not allowed")
        return list(self.notes_for_directory.get(directory_id, []))[offset : offset + limit]

    async def get_directory(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
        *,
        include=None,
    ) -> Optional[DirectoryEntity]:
        self.last_get_directory_user_id = user_ctx.user_id
        if self.get_directory_deny:
            raise PermissionError("not allowed")
        return self.directories_by_id.get(directory_id)

    async def get_directories(
        self,
        user_ctx: UserContextABC,
        parent_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        *,
        include=None,
    ) -> List[DirectoryEntity]:
        self.last_get_directories_user_id = user_ctx.user_id
        self.get_directories_parent_id = parent_id
        self.get_directories_limit = limit
        self.get_directories_offset = offset
        if self.get_directories_deny:
            raise PermissionError("not allowed")

        items = list(self.directories_for_user.get(user_ctx.user_id, []))
        if parent_id is not None:
            items = [
                d
                for d in items
                if d.parent_directory_ids not in (UNDEFINED, None)
                and parent_id in {
                    str(p) for p in (d.parent_directory_ids or []) if p
                }
            ]
        effective_offset = offset if offset is not None else 0
        if limit is not None:
            return items[effective_offset : effective_offset + limit]
        return items[effective_offset:]

    async def create_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: UserContextABC,
    ) -> DirectoryEntity:
        self.last_create_entity = entity
        self.last_create_user_id = user_ctx.user_id
        if self.create_deny:
            raise PermissionError("not allowed")
        self.next_directory_id += 1
        new_id = f"dir-{self.next_directory_id}"
        created = DirectoryEntity(
            id=new_id,
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            parent_directory_ids=list(entity.parent_directory_ids or []),
            relations=entity.relations,
        )
        self.directories_by_id[new_id] = created
        self.directories_for_user.setdefault(user_ctx.user_id, []).append(created)
        return created

    async def patch_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: UserContextABC,
    ) -> Optional[DirectoryEntity]:
        self.last_patch_entity = entity
        self.last_patch_user_id = user_ctx.user_id
        if self.patch_deny:
            raise PermissionError("not allowed")
        if self.patch_result is not None:
            return self.patch_result
        existing = self.directories_by_id.get(str(entity.id))
        if existing is None:
            return None
        updated = DirectoryEntity(
            id=existing.id,
            slug=existing.slug if entity.slug is UNDEFINED else entity.slug,
            display_name=existing.display_name if entity.display_name is UNDEFINED else entity.display_name,
            description=existing.description if entity.description is UNDEFINED else entity.description,
            image_url=existing.image_url if entity.image_url is UNDEFINED else entity.image_url,
            parent_directory_ids=(
                existing.parent_directory_ids if entity.parent_directory_ids is UNDEFINED
                else list(entity.parent_directory_ids or [])
            ),
            relations=existing.relations,
        )
        self.directories_by_id[str(entity.id)] = updated
        return updated

    async def delete_directory(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> bool:
        self.last_delete_id = directory_id
        self.last_delete_user_id = user_ctx.user_id
        if self.delete_deny:
            raise PermissionError("not allowed")
        if directory_id in self.directories_by_id:
            del self.directories_by_id[directory_id]
        return self.delete_result

    async def dry_delete(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> List[DirectoryChild]:
        self.last_delete_id = directory_id
        self.last_delete_user_id = user_ctx.user_id
        if self.delete_deny:
            raise PermissionError("not allowed")
        # The stub has no resolver; tests that exercise the real
        # recursive-delete behaviour wire the production
        # ``DirectoryServiceImpl`` directly.
        return []


__all__ = ["_StubDirectoryService"]
