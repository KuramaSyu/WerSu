"""Concrete :class:`~src.api.services.user_service.UserServiceABC` implementation."""

from __future__ import annotations

from typing import Optional

from src.api.other.undefined import is_undefined, unwrap_undefined
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.services.user_service import UserServiceABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.user.user import UserEntity
from src.db.repos.directory.directory_facade import DirectoryFacadeABC
from src.db.repos.user.user import UserRepoABC


class UserServiceImpl(UserServiceABC):
    """Application service for user lifecycle and bootstrap directories."""

    def __init__(
        self,
        user_repo: UserRepoABC,
        directory_facade: DirectoryFacadeABC,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._user_repo = user_repo
        self._directory_facade = directory_facade
        self._context_factory = context_factory

    async def get_user(
        self,
        user_id: Optional[str] = None,
        discord_id: Optional[int] = None,
    ) -> Optional[UserEntity]:
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

        for spec in self._directory_facade.get_default_directory_specs():
            await self._directory_facade.create_directory(
                DirectoryEntity(
                    slug=spec.name,
                    display_name=spec.display_name,
                    description=spec.description,
                    relations=[],
                ),
                user_ctx,
            )

        return created_user

    async def update_user(self, user: UserEntity) -> UserEntity:
        """Persist partial updates without touching the directory bootstrap.

        Forwards directly to the repo so the gRPC auth adapter
        can write a single column (e.g. ``avatar``) without
        re-running the directory creation side effects of
        :meth:`create_user`.
        """
        return await self._user_repo.update(user)


__all__ = ["UserServiceImpl"]