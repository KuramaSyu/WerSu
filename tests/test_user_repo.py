from dataclasses import replace
from typing import AsyncGenerator, Optional
import pytest
from sqlalchemy import update
from testcontainers.postgres import PostgresContainer
from src.db.entities.user.user import UserEntity
from src.db.repos.user.user import UserRepoABC
import src.api
from src.db.repos import UserPostgresRepo, Database
from src.utils import logging_provider
from .fixtures import db, user_repo



async def test_create_user(db: Database, user_repo: UserRepoABC):
    """Creates a test user and retrieves it by discord_id"""
    test_user = UserEntity(
        discord_id=123455,
        avatar_url="test",
    )
    await user_repo.insert(test_user)
    ret_user = await user_repo.select_by_discord_id(test_user.discord_id)
    assert ret_user
    assert ret_user.avatar_url == test_user.avatar_url

async def test_update_user(db: Database, user_repo: UserRepoABC):
    """Creates a test user, updates it, and retrieves it once by discord_id and once by id"""
    test_user = UserEntity(
        discord_id=123455,
        avatar_url="test",
    )
    await user_repo.insert(test_user)
    updated_user = replace(test_user, avatar_url="http://somewere")
    ret_user_update = await user_repo.update(updated_user)
    ret_user_discord = await user_repo.select_by_discord_id(updated_user.discord_id)
    assert ret_user_discord and ret_user_discord.id
    assert ret_user_discord == ret_user_update  # assert that update returns same as select
    assert ret_user_discord == updated_user  # now also id should match
    ret_user_by_id = await user_repo.select(ret_user_discord.id)
    assert ret_user_by_id == ret_user_discord  # both selects should return same user


