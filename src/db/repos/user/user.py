from abc import ABC, abstractmethod
from typing import Optional

from db.entities import UserEntity
from db import Database


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
    async def select(self, user_id: int) -> Optional[UserEntity]:
        """Select a user by ID."""
        pass

    @abstractmethod
    async def select_by_discord_id(self, discord_id: int) -> Optional[UserEntity]:
        """Select a user by discord_id."""
        pass

    @abstractmethod
    async def delete(self, user_id: int) -> bool:
        """Delete a user by ID."""
        pass


class UserRepository(UserRepoABC):
    def __init__(self, db: Database):
        self.db = db

    async def insert(self, user: UserEntity) -> UserEntity:
        """Insert a new user and return the created entity with ID."""
        query = "INSERT INTO users (discord_id, avatar_url) VALUES ($1, $2) RETURNING id"
        user_id = await self.db.fetchrow(query, user.discord_id, user.avatar_url)
        user.id = user_id
        return user

    async def update(self, user: UserEntity) -> UserEntity:
        """Update an existing user."""
        if user.id is None:
            raise ValueError("User ID is required for update operation")
        query = "UPDATE users SET discord_id = $1, avatar_url = $2 WHERE id = $3 RETURNING id"
        await self.db.fetchrow(query, user.discord_id, user.avatar_url, user.id)
        return user

    async def upsert(self, user: UserEntity) -> UserEntity:
        """Insert or update a user based on discord_id."""
        query = """
            INSERT INTO users (discord_id, avatar_url) 
            VALUES ($1, $2) 
            ON CONFLICT (discord_id) DO UPDATE 
            SET avatar_url = $2 
            RETURNING id
        """
        user_id = await self.db.fetchrow(query, user.discord_id, user.avatar_url)
        user.id = user_id
        return user

    async def select(self, user_id: int) -> Optional[UserEntity]:
        """Select a user by ID."""
        query = "SELECT id, discord_id, avatar_url FROM users WHERE id = $1"
        row = await self.db.fetchrow(query, user_id)
        if row:
            return UserEntity(
                id=row["id"],
                discord_id=row["discord_id"],
                avatar_url=row["avatar_url"]
            )
        return None

    async def select_by_discord_id(self, discord_id: int) -> Optional[UserEntity]:
        """Select a user by discord_id."""
        query = "SELECT id, discord_id, avatar_url FROM users WHERE discord_id = $1"
        row = await self.db.fetchrow(query, discord_id)
        if row:
            return UserEntity(
                id=row["id"],
                discord_id=row["discord_id"],
                avatar_url=row["avatar_url"]
            )
        return None

    async def delete(self, user_id: int) -> bool:
        """Delete a user by ID."""
        query = "DELETE FROM users WHERE id = $1"
        result = await self.db.execute(query, user_id)
        return result == "DELETE 1"
