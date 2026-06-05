import asyncio
from pathlib import Path
from typing import AsyncIterator
import uuid

import pytest
from authzed.api.v1 import AsyncClient, WriteSchemaRequest
from grpcutil import insecure_bearer_token_credentials
from testcontainers_spicedb import SpiceDBContainer

from src.db.repos.directory.directory import DirectoryRepoSpicedbPostgres
from src.db.repos.note.note import UserContext
from src.api import (
    NotePermissionRepoSpicedb,
    ObjectRef,
    Relationship,
    SubjectRef,
)


pytestmark = [pytest.mark.integration, pytest.mark.spicedb]

SPICEDB_IMAGE = "authzed/spicedb:v1.47.1"


def load_spicedb_schema() -> str:
    schema_path = Path(__file__).resolve().parents[1] / "src" / "db" / "migrations" / "schema.zed"
    return schema_path.read_text(encoding="utf-8")


def create_client(endpoint: str, secret_key: str) -> AsyncClient:
    return AsyncClient(endpoint, insecure_bearer_token_credentials(secret_key))


async def wait_until_spicedb_ready(client: AsyncClient, schema: str) -> None:
    attempts = 30
    retry_delay_s = 0.5
    last_error: Exception | None = None

    for _ in range(attempts):
        try:
            await client.WriteSchema(WriteSchemaRequest(schema=schema))
            return
        except Exception as exc:  # pragma: no cover - only reached during startup race
            last_error = exc
            await asyncio.sleep(retry_delay_s)

    raise RuntimeError("SpiceDB container did not become ready in time") from last_error


@pytest.fixture(scope="function")
async def permission_repo() -> AsyncIterator[NotePermissionRepoSpicedb]:
    with SpiceDBContainer(image=SPICEDB_IMAGE) as spicedb:
        client = create_client(spicedb.get_endpoint(), spicedb.get_secret_key())
        await wait_until_spicedb_ready(client, load_spicedb_schema())
        yield NotePermissionRepoSpicedb(client=client)


async def test_resolve_files_of_directory_spicedb(permission_repo: NotePermissionRepoSpicedb) -> None:
    user_id = "spicedb-user"
    root_id = f"root-{uuid.uuid4().hex}"
    child_id = f"child-{uuid.uuid4().hex}"
    note_root = f"note-root-{uuid.uuid4().hex}"
    note_child = f"note-child-{uuid.uuid4().hex}"

    relationships = [
        Relationship(
            resource=ObjectRef(object_type="directory", object_id=root_id),
            relation="reader",
            subject=SubjectRef(object_type="user", object_id=user_id),
        ),
        Relationship(
            resource=ObjectRef(object_type="directory", object_id=child_id),
            relation="parent",
            subject=SubjectRef(object_type="directory", object_id=root_id),
        ),
        Relationship(
            resource=ObjectRef(object_type="note", object_id=note_root),
            relation="parent_directory",
            subject=SubjectRef(object_type="directory", object_id=root_id),
        ),
        Relationship(
            resource=ObjectRef(object_type="note", object_id=note_child),
            relation="parent_directory",
            subject=SubjectRef(object_type="directory", object_id=child_id),
        ),
    ]
    await permission_repo.insert(relationships)

    directory_repo = DirectoryRepoSpicedbPostgres(
        db=None,  # type: ignore[arg-type]
        permission_repo=permission_repo,
        spicedb_client=permission_repo.client,
    )

    resolved = await directory_repo.resolve_files_of_directory(
        directory_id=root_id,
        actor=UserContext(user_id=user_id),
        max_depth=3,
    )

    assert set(resolved) == {note_root, note_child}
