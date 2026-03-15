import asyncio
from pathlib import Path
from typing import AsyncIterator
import uuid

import pytest
from authzed.api.v1 import AsyncClient, WriteSchemaRequest
from grpcutil import insecure_bearer_token_credentials
from testcontainers_spicedb import SpiceDBContainer

from src.db.repos.note.note import UserContext
from src.db.repos.note.permission import (
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
async def note_permissions_repo() -> AsyncIterator[NotePermissionRepoSpicedb]:
    with SpiceDBContainer(image=SPICEDB_IMAGE) as spicedb:
        client = create_client(spicedb.get_endpoint(), spicedb.get_secret_key())
        await wait_until_spicedb_ready(client, load_spicedb_schema())
        yield NotePermissionRepoSpicedb(client=client)


async def test_note_insert_and_check(note_permissions_repo: NotePermissionRepoSpicedb):
    emilia = SubjectRef(object_type="user", object_id="emilia")
    alfred = SubjectRef(object_type="user", object_id="alfred")

    note_id = f"note-{uuid.uuid4().hex}"
    note = ObjectRef(object_type="note", object_id=note_id)

    relationships = [
        Relationship(resource=note, relation="admin", subject=emilia),
        Relationship(resource=note, relation="reader", subject=alfred),
    ]

    inserted = await note_permissions_repo.insert(relationships)
    assert len(inserted) == 2

    emilia_notes = await note_permissions_repo.lookup_notes(UserContext("emilia"), "view")
    assert [obj.object_id for obj in emilia_notes] == [note_id]

    alfred_notes = await note_permissions_repo.lookup_notes(UserContext("alfred"), "view")
    assert [obj.object_id for obj in alfred_notes] == [note_id]

    alfred_admin_notes = await note_permissions_repo.lookup_notes(UserContext("alfred"), "admin")
    assert [obj.object_id for obj in alfred_admin_notes] == []


async def test_note_missing_permissions(note_permissions_repo: NotePermissionRepoSpicedb):
    emilia = SubjectRef(object_type="user", object_id="emilia")
    alfred = SubjectRef(object_type="user", object_id="alfred")

    reader_note_id = f"note-{uuid.uuid4().hex}"
    admin_note_id = f"note-{uuid.uuid4().hex}"
    reader_note = ObjectRef(object_type="note", object_id=reader_note_id)
    admin_note = ObjectRef(object_type="note", object_id=admin_note_id)

    inserted = await note_permissions_repo.insert(
        [
            Relationship(resource=reader_note, relation="reader", subject=emilia),
            Relationship(resource=admin_note, relation="admin", subject=alfred),
        ]
    )
    assert len(inserted) == 2

    # Emilia can view her reader note, but is not admin there.
    assert await note_permissions_repo.has_permission(UserContext("emilia"), "view", reader_note)
    assert not await note_permissions_repo.has_permission(UserContext("emilia"), "admin", reader_note)

    # Emilia has no permissions at all on Alfred's note.
    assert not await note_permissions_repo.has_permission(UserContext("emilia"), "view", admin_note)
    assert not await note_permissions_repo.has_permission(UserContext("emilia"), "admin", admin_note)
    assert await note_permissions_repo.get_permissions(UserContext("emilia"), admin_note) == []

