from __future__ import annotations

import asyncio
from dataclasses import dataclass
from re import sub
from typing import Any, Dict, Optional, Sequence, Tuple

import grpc

from authzed.api.v1 import (
    BulkExportRelationshipsRequest,
    BulkImportRelationshipsRequest,
    Client,
    Consistency,
    ObjectReference,
    Relationship,
    SubjectReference,
    WriteSchemaRequest,
)
from grpcutil import insecure_bearer_token_credentials


SCHEMA_ZED: str = """
definition user {}

definition directory {
    relation parent: directory
    relation admin: user
    relation writer: user
    relation reader: user

    permission delete = admin
    permission write = writer + admin
    permission view = reader + write
}

definition note {
    relation admin: user
    relation writer: user
    relation reader: user
    relation parent_directory: directory

    permission delete = admin + parent_directory->delete
    permission write = writer + admin + parent_directory->write
    permission view = reader + write
}
"""

def get_client() -> Client:
    return Client(
        "localhost:50051",
        insecure_bearer_token_credentials("somerandomkeyhere")
    )

# real system would use actual IDs as object_id
elimia = SubjectReference(
    object=ObjectReference(
        object_type="user",
        object_id="emilia"
    )
)

alfred = SubjectReference(
    object=ObjectReference(
        object_type="user",
        object_id="alfred"
    )
)

daily_note = ObjectReference(
    object_type="note",
    object_id="daily_note_2026-03-10"
)

client = get_client()
client.WriteSchema(WriteSchemaRequest(schema=SCHEMA_ZED))

requests = [
    BulkImportRelationshipsRequest(
        relationships=[
            Relationship(
                resource=daily_note,
                relation="admin",
                subject=alfred,
            ),
            Relationship(
                resource=daily_note,
                relation="reader",
                subject=elimia
            )
        ]
    )
]

import_requests = client.BulkImportRelationships((req for req in requests))
assert import_requests.num_loaded == 2

export_requests = client.BulkExportRelationships(
    BulkExportRelationshipsRequest(
        consistency=Consistency(fully_consistent=True)
    )
)

relationships = []
for resp in export_requests:
    for rel in resp.relationships:
        relationships.append(rel)

assert len(relationships) == 2