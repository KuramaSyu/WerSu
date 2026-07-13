from abc import ABC, abstractmethod
from typing import Optional

from src.api.undefined import is_undefined, unwrap_undefined
from src.api.user_context import ContextFactory, UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.user.user import UserEntity
from src.db.repos.directory.directory import DirectoryFacade

from src.db.repos.user.user import UserRepoABC


class UserServiceABC(ABC):
    @abstractmethod
    async def get_user(self, user_id: Optional[str] = None, discord_id: Optional[int] = None) -> Optional[UserEntity]:
        """
        Creates a user and, in case that user is of type "human", also creates the default directories with admin relation for that user.
        """
        ...

    @abstractmethod
    async def create_user(self, user: UserEntity) -> UserEntity:
        ...


class UserService(UserServiceABC):
    """Application service for user lifecycle and bootstrap directories."""

    def __init__(self, user_repo: UserRepoABC, directory_repo: DirectoryFacade, context_factory: ContextFactory[UserContextABC]) -> None:
        self._user_repo = user_repo
        self._directory_repo = directory_repo
        self._context_factory = context_factory

    async def get_user(self, user_id: Optional[str] = None, discord_id: Optional[int] = None) -> Optional[UserEntity]:
        if user_id is not None:
            return await self._user_repo.select(user_id=user_id)
        if discord_id is not None:
            return await self._user_repo.select_by_discord_id(discord_id=discord_id)
        return None

    async def create_user(self, user: UserEntity) -> UserEntity:
        created_user = await self._user_repo.insert(user)
        user_id = unwrap_undefined(created_user.id)

        if is_undefined(user.type) or user.type in ["temporary", "system"]:
            return created_user

        user_ctx = await self._context_factory.create(user_id)

        for spec in self._directory_repo.get_default_directory_specs():
            await self._directory_repo.create_directory(
                DirectoryEntity(
                    slug=spec.name,
                    display_name=spec.display_name,
                    description=spec.description,
                    relations=[],
                ),
                user_ctx
            )

        return created_user
