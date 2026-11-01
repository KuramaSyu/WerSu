from __future__ import annotations

from dataclasses import replace
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.relationship import ObjectRef, Relationship, SubjectRef
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import ContextFactory, UserContextABC
from tests.doubles.dummies.user_context import _DummyUserContext
from src.api.repos.directory_repo import DirectoryChildType, DirectoryHierarchyType, DirectoryParentType
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.user.user import FilterUser, UserEntity
from src.api.other.relationship import DirectoryRelationEnum, ObjectTypeEnum
from src.db.repos.user.user import UserRepoABC
from typing import Dict, List, Optional, Tuple


class _InMemoryUserRepo(UserRepoABC):
    def __init__(self) -> None:
        self._by_id: Dict[str, UserEntity] = {}
        self._by_discord_id: Dict[int, str] = {}
        self._counter = 1

    async def insert(self, user: UserEntity) -> UserEntity:
        user_id = f"user-{self._counter}"
        self._counter += 1
        created = replace(user, id=user_id)
        self._by_id[user_id] = created
        self._by_discord_id[created.discord_id] = user_id
        return created

    async def update(self, user: UserEntity) -> UserEntity:
        if user.id is None:
            raise ValueError("User ID is required for update operation")
        self._by_id[user.id] = user
        self._by_discord_id[user.discord_id] = user.id
        return user

    async def upsert(self, user: UserEntity) -> UserEntity:
        existing = await self.select_by_discord_id(user.discord_id)
        if existing is None:
            return await self.insert(user)
        updated = replace(user, id=existing.id)
        return await self.update(updated)

    async def select(self, filter: FilterUser) -> Optional[UserEntity]:
        if filter.is_empty():
            return None
        for user in self._by_id.values():
            if (
                filter.user_id is not UNDEFINED
                and str(user.id) != str(filter.user_id)
            ):
                continue
            if (
                filter.username is not UNDEFINED
                and user.username != filter.username
            ):
                continue
            return user
        return None

    async def select_by_discord_id(self, discord_id: int) -> Optional[UserEntity]:
        user_id = self._by_discord_id.get(discord_id)
        if user_id is None:
            return None
        return self._by_id[user_id]

    async def delete(self, user_id: str) -> bool:
        user = self._by_id.pop(user_id, None)
        if user is None:
            return False
        self._by_discord_id.pop(user.discord_id, None)
        return True


class _InMemoryDirectoryRepo(DirectoryFacadeABC):
    def __init__(self) -> None:
        self.created: List[DirectoryEntity] = []

    async def create_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: Optional[UserContextABC] = None,
    ) -> DirectoryEntity:
        created = replace(entity, id=f"dir-{len(self.created) + 1}")
        # Mirror production :class:`DirectoryFacadeImpl` behaviour:
        # always attach a `dir#admin@user` relation for the caller.
        if user_ctx is not None:
            admin_rel = Relationship(
                resource=ObjectRef(
                    object_type=ObjectTypeEnum.DIRECTORY,
                    object_id=str(created.id),
                ),
                relation=DirectoryRelationEnum.ADMIN,
                subject=SubjectRef(
                    object_type=ObjectTypeEnum.USER,
                    object_id=str(user_ctx.user_id),
                ),
            )
            existing = list(created.relations or [])
            existing.append(admin_rel)
            created = replace(created, relations=existing)
        self.created.append(created)
        return created

    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        for directory in self.created:
            if directory.id == id:
                return directory
        return None

    async def update_directory(self, entity: DirectoryEntity) -> Optional[DirectoryEntity]:
        return entity

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        return [str(directory.id) for directory in self.created if directory.id is not UNDEFINED]

    async def fetch_all_directories(self) -> List[DirectoryEntity]:
        return list(self.created)

    async def fetch_directories_by_ids(
        self, ids: List[str],
    ) -> List[DirectoryEntity]:
        # Mirror :meth:`fetch_directory`: linear scan over the
        # in-memory ``created`` list.  Missing ids silently drop.
        out: List[DirectoryEntity] = []
        for did in ids:
            for d in self.created:
                if str(d.id) == str(did):
                    out.append(d)
                    break
        return out

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        return False

    async def resolve_files_of_directory(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
    ) -> List[str]:
        return []

    async def resolve_subtree(
        self,
        directory_id: str,
        max_depth: int = 10,
    ) -> Tuple[List[str], List[str]]:
        return ([], [directory_id])

    # ---- DirectoryHelperMixin: hierarchy helpers (no-op stubs) ------

    async def set_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
    ) -> None:
        return None

    async def get_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
    ) -> List[str]:
        return []

    async def get_children_of(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> List[str]:
        return []

    async def get_children_for(
        self,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> Dict[str, List[str]]:
        return {str(d): [] for d in parent_ids}

    async def get_parents_for(
        self,
        child_type: DirectoryChildType,
        child_ids: List[str],
        parent_type: DirectoryParentType,
    ) -> Dict[str, List[str]]:
        return {str(c): [] for c in child_ids}

    async def add_child_to(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        return None

    async def remove_child_from(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        return None


class _InMemoryContextFactory(ContextFactory[UserContextABC]):
    """In-memory ContextFactory used by unit tests."""

    async def create(self, user_id: str) -> UserContextABC:
        return _DummyUserContext(user_id=user_id)


__all__ = [
    '_InMemoryUserRepo',
    '_InMemoryDirectoryRepo',
    '_InMemoryContextFactory',
]
