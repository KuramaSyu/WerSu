from abc import ABC, abstractmethod
from typing import Optional

from src.api.undefined import UNDEFINED
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.user.user import UserEntity
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.permission import (
    DirectoryRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.db.repos.user.user import UserRepoABC


class UserServiceABC(ABC):
    @abstractmethod
    async def get_user(self, user_id: Optional[str] = None, discord_id: Optional[int] = None) -> Optional[UserEntity]:
        ...

    @abstractmethod
    async def create_user(self, user: UserEntity) -> UserEntity:
        ...


class UserServiceRepo(UserServiceABC):
    """Application service for user lifecycle and bootstrap directories."""

    def __init__(self, user_repo: UserRepoABC, directory_repo: DirectoryRepo):
        self._user_repo = user_repo
        self._directory_repo = directory_repo

    async def get_user(self, user_id: Optional[str] = None, discord_id: Optional[int] = None) -> Optional[UserEntity]:
        if user_id is not None:
            return await self._user_repo.select(user_id=user_id)
        if discord_id is not None:
            return await self._user_repo.select_by_discord_id(discord_id=discord_id)
        return None

    async def create_user(self, user: UserEntity) -> UserEntity:
        created_user = await self._user_repo.insert(user)
        assert created_user.id is not None

        admin_relation = Relationship(
            resource=ObjectRef(object_type=ObjectTypeEnum.DIRECTORY, object_id=UNDEFINED),
            relation=DirectoryRelationEnum.ADMIN,
            subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id=str(created_user.id)),
        )

        for spec in self._directory_repo.get_default_directory_specs():
            await self._directory_repo.create_directory(
                DirectoryEntity(
                    name=spec.name,
                    display_name=spec.display_name,
                    description=spec.description,
                    relations=[admin_relation],
                )
            )

        return created_user
