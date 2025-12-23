import pytest
from testcontainers.core.container import DockerContainer
from api.types import LoggingProvider
from db.repos import UserPostgresRepo, Database
from utils import logging_provider

@pytest.fixture(scope='session')
def postgres_container():
    container = DockerContainer("pgvector/pgvector:pg16")
    container.start()
    yield container
    container.stop()

async def test(postgres_container):
    db_url = f"postgresql://{postgres_container.get_connection_uri()}"
    db = Database(db_url, logging_provider)

    # run init.sql which creates tables
    await db.init_db()
    await db.execute("INSERT INTO users (name) VALUES ('Paul Zenker');")

