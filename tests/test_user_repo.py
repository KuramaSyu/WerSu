from dataclasses import replace
from typing import AsyncGenerator, Optional
import pytest
from testcontainers.postgres import PostgresContainer
from src.db.entities.user.user import UserEntity
from src.db.repos.user.user import UserRepoABC
import src.api
from src.db.repos import UserPostgresRepo, Database
from src.utils import logging_provider

def create_postgres_dsn(postgres_container: PostgresContainer) -> str:
    return (
        f"postgresql://"
        f"{postgres_container.username}:"
        f"{postgres_container.password}@"
        f"{postgres_container.get_container_host_ip()}:"
        f"{postgres_container.get_exposed_port(5432)}/"
        f"{postgres_container.dbname}"
    )

@pytest.fixture
async def db():
    container = PostgresContainer(
        image="pgvector/pgvector:pg16",
        username="postgres",
        password="postgres",
        dbname="testdb",
    )
    container.start()
    dsn = create_postgres_dsn(container)
    db = Database(dsn, logging_provider, init_file="src/init.sql")
    await db.init_db()
    yield db
    await db.close()
    container.stop()


async def test_create_user(db: Database):
    repo: UserRepoABC = UserPostgresRepo(db)
    test_user = UserEntity(
        discord_id=123455,
        avatar_url="test",
    )
    await repo.insert(test_user)
    ret_user = await repo.select_by_discord_id(test_user.discord_id)
    assert ret_user
    assert ret_user.avatar_url == test_user.avatar_url

async def test_update_user(db: Database):
    repo: UserRepoABC = UserPostgresRepo(db)
    test_user = UserEntity(
        discord_id=123455,
        avatar_url="test",
    )
    await repo.insert(test_user)
    updated_user = replace(test_user, avatar_url="http://somewere")
    await repo.update(updated_user)
    ret_user = await repo.select_by_discord_id(updated_user.discord_id)
    assert ret_user
    assert ret_user == updated_user  # now also id should match



