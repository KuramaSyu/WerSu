from abc import ABC, abstractmethod
from typing import Optional

from src.db.entities import UserEntity
from src.db import Database
from src.utils.logging import logging_provider


class UserRepoABC(ABC):
    @abstractmethod
    async def insert(self, user: UserEntity) -> UserEntity:
        """Insert a new user and return the created entity with ID."""
        pass

    @abstractmethod
    async def update(self, user: UserEntity) -> UserEntity:
        """Update an existing user."""
        pass

    @abstractmethod
    async def upsert(self, user: UserEntity) -> UserEntity:
        """Insert or update a user based on discord_id."""
        pass

    @abstractmethod
    async def select(self, user_id: str) -> Optional[UserEntity]:
        """Select a user by ID."""
        pass

    @abstractmethod
    async def select_by_discord_id(self, discord_id: int) -> Optional[UserEntity]:
        """Select a user by discord_id."""
        pass

    @abstractmethod
    async def delete(self, user_id: str) -> bool:
        """Delete a user by ID."""
        pass


class UserPostgresRepo(UserRepoABC):
    """Provides an impementation using Postgres as the backend database"""
    def __init__(self, db: Database):
        self.db = db

    async def insert(self, user: UserEntity) -> UserEntity:
        """Insert a new user and return the created entity with ID."""
        query = "INSERT INTO users (discord_id, avatar, username, discriminator, email) VALUES ($1, $2, $3, $4, $5) RETURNING id"
        user_id = await self.db.fetchrow(query, user.discord_id, user.avatar, user.username, user.discriminator, user.email)
        user.id = user_id["id"]
        return user

    async def update(self, user: UserEntity) -> UserEntity:
        """Update an existing user."""
        if user.id is None:
            raise ValueError("User ID is required for update operation")
        query = "UPDATE users SET discord_id = $1, avatar = $2 WHERE id = $3 RETURNING *"
        ret = await self.db.fetchrow(query, user.discord_id, user.avatar, user.id)
        if not ret:
            raise Exception(f"Failed to update user; returned: {ret}")
        return UserEntity(**ret)

    async def upsert(self, user: UserEntity) -> UserEntity:
        """Insert or update a user based on discord_id."""
        query = """
            INSERT INTO users (discord_id, avatar, username, discriminator, email) 
            VALUES ($1, $2, $3, $4, $5) 
            ON CONFLICT (discord_id) DO UPDATE 
            SET avatar = $2, username = $3, discriminator = $4, email = $5
            RETURNING id
        """
        user_id = await self.db.fetchrow(query, user.discord_id, user.avatar, user.username, user.discriminator, user.email)
        user.id = user_id["id"]
        return user

    async def select(self, user_id: str) -> Optional[UserEntity]:
        """Select a user by ID."""
        query = "SELECT id, discord_id, avatar, username, discriminator, email FROM users WHERE id = $1"
        row = await self.db.fetchrow(query, user_id)
        if row:
            return UserEntity(
                id=row["id"],
                discord_id=row["discord_id"],
                avatar=row["avatar"],
                username=row["username"],
                discriminator=row["discriminator"],
                email=row["email"]
            )
        return None

    async def select_by_discord_id(self, discord_id: int) -> Optional[UserEntity]:
        """Select a user by discord_id."""
        query = "SELECT id, discord_id, avatar, username, discriminator, email FROM users WHERE discord_id = $1"
        row = await self.db.fetchrow(query, discord_id)
        if row:
            return UserEntity(
                id=row["id"],
                discord_id=row["discord_id"],
                avatar=row["avatar"],
                username=row["username"],
                discriminator=row["discriminator"],
                email=row["email"]
            )
        return None

    async def delete(self, user_id: str) -> bool:
        """Delete a user by ID."""
        query = "DELETE FROM users WHERE id = $1"
        result = await self.db.execute(query, user_id)
        return result == "DELETE 1"
