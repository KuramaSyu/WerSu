# test which needs a real spicedb instance running

import pytest
from typing import Iterator
from src.db.repos import NotePermissionRepo, NotePermissionRepoSpicedb

from authzed.api.v1 import (
    BulkExportRelationshipsRequest,
    BulkImportRelationshipsRequest,
    CheckPermissionRequest,
    CheckPermissionResponse,
    AsyncClient,
    Consistency,
    LookupResourcesRequest,
    ObjectReference,
    SubjectReference,
    WriteSchemaRequest,
)
from grpcutil import insecure_bearer_token_credentials

from src.db.repos.note.note import UserContext
from src.db.repos.note.permission import ObjectRef, Relationship, SubjectRef

def get_client() -> AsyncClient:
    return AsyncClient(
        "localhost:50051",
        insecure_bearer_token_credentials("somerandomkeyhere")
    )

@pytest.fixture
def note_permissions_repo() -> NotePermissionRepoSpicedb:
    return NotePermissionRepoSpicedb(
        client=get_client()
    )

async def test_note_insert_and_check(perm_repo: NotePermissionRepoSpicedb):
    # real system would use actual IDs as object_id
    emilia = SubjectRef(
        object_type="user",
        object_id="emilia"
    )
    alfred = SubjectRef(
        object_type="user",
        object_id="alfred"
    )

    emilias_note = ObjectRef(
        object_type="note",
        object_id="note1"
    )

    relationships = [
        Relationship(
            resource=emilias_note,
            relation="admin",
            subject=emilia
        ), 
        Relationship(
            resource=emilias_note,
            relation="viewer",
            subject=alfred
        )
    ]

    inserted = await perm_repo.insert(relationships)
    assert inserted == 2

    notes = await perm_repo.lookup_notes(UserContext("emilia"), "view")
    assert notes == ["note1"]
    notes = await perm_repo.lookup_notes(UserContext("alfred"), "view")
    assert notes == ["note1"]
    notes = await perm_repo.lookup_notes(UserContext("alfred"), "admin")
    assert notes == []