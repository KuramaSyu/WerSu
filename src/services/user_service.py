"""Concrete :class:`~src.api.services.user_service.UserServiceABC` implementation."""

from __future__ import annotations

from typing import List, Optional

from src.api.other.undefined import UNDEFINED, is_undefined, unwrap_undefined
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.services.shelf_service import (
    BootstrapStrategy,
    ShelfServiceABC,
)
from src.api.services.user_service import UserServiceABC
from src.db.entities.shelf import ShelfEntity
from src.db.entities.user.user import UserEntity
from src.db.repos.directory.directory_facade import DirectoryFacadeABC
from src.db.repos.user.user import UserRepoABC


def users_shelf_slug_for(username: object) -> str:
    """Build the per-user shelf slug, e.g. ``<username>'s shelf``. Falls back to ``"user shelf"``."""
    if not username or not isinstance(username, str):
        return "user shelf"
    base = username.strip().lower().replace("/", "-")
    cleaned = "-".join(part for part in base.split() if part)
    return f"{cleaned}'s shelf" if cleaned else "user shelf"


def users_shelf_display_name_for(username: object) -> str:
    """Build the per-user shelf display name, e.g. ``<Username>'s Shelf``."""
    if not username or not isinstance(username, str):
        return "User's Shelf"
    cleaned = " ".join(part for part in username.strip().split() if part)
    return f"{cleaned}'s Shelf" if cleaned else "User's Shelf"


USERS_SHELF_SLUG = "users_shelf"
"""Legacy default slug for single-tenant deployments; new code should call :func:`users_shelf_slug_for`."""

USERS_SHELF_DESCRIPTION = (
    "Default shelf grouping the zettelkasten "
    "books every user starts with."
)


class UserServiceImpl(UserServiceABC):
    """User lifecycle + zettelkasten bootstrap service.

    create_user inserts the row, then delegates shelf creation
    to ShelfServiceImpl.create_shelf with ZETTELKASTEN bootstrap.
    temporary/system users skip the bootstrap entirely.
    """

    def __init__(
        self,
        user_repo: UserRepoABC,
        directory_facade: DirectoryFacadeABC,
        context_factory: ContextFactory[UserContextABC],
        shelf_service: ShelfServiceABC,
    ) -> None:
        self._user_repo = user_repo
        self._directory_facade = directory_facade
        self._context_factory = context_factory
        self._shelf_service = shelf_service

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
        """Create the user, then bootstrap their shelf + books + rule."""
        created_user = await self._user_repo.insert(user)
        user_id = unwrap_undefined(created_user.id)

        # temporary/system users get the row but no shelf/books/rule.
        if is_undefined(user.type) or user.type in ["temporary", "system"]:
            return created_user

        await self._bootstrap_user_zettelkasten(
            user_id=str(user_id),
            username=created_user.username,
        )
        return created_user

    async def update_user(self, user: UserEntity) -> UserEntity:
        """Forward directly to the repo; no directory side effects."""
        return await self._user_repo.update(user)

    async def _bootstrap_user_zettelkasten(
        self,
        *,
        user_id: str,
        username: UndefinedNoneOr[str] = UNDEFINED,
    ) -> None:
        """Build a shelf entity and call ShelfServiceImpl.create_shelf with ZETTELKASTEN.

        The shelf service owns the row + owner/admin relations and
        runs the strategy (idempotent on its own; this method is
        NOT idempotent -- re-running create_user for an existing
        user creates duplicates).
        """
        user_ctx = await self._context_factory.create(user_id)

        shelf_username = username or user_id
        shelf_entity = ShelfEntity(
            slug=users_shelf_slug_for(shelf_username),
            display_name=users_shelf_display_name_for(shelf_username),
            description=USERS_SHELF_DESCRIPTION,
        )

        await self._shelf_service.create_shelf(
            shelf_entity,
            user_ctx,
            bootstrap=BootstrapStrategy.ZETTELKASTEN,
        )


__all__ = [
    "UserServiceImpl",
    "USERS_SHELF_SLUG",
    "USERS_SHELF_DESCRIPTION",
    "users_shelf_slug_for",
    "users_shelf_display_name_for",
]
