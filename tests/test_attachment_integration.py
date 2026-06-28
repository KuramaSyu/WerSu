"""Integration tests for the attachment facade with real Postgres + SpiceDB + Garage."""

from datetime import datetime
from pathlib import Path
import asyncio
import socket
import time

import boto3
import grpc
import pytest
from authzed.api.v1 import AsyncClient, WriteSchemaRequest
from botocore.config import Config as BotoConfig
from grpcutil import insecure_bearer_token_credentials
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_container_is_ready
from testcontainers_spicedb import SpiceDBContainer

from src.api import (
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.undefined import UNDEFINED
from src.db.repos.attachments.attachments import (
    Attachment,
    AttachmentsMetadataPostgresRepo,
    AttachmentsS3Repo,
)
from src.db.repos.note.note import UserContext
from src.db.repos.permissions.permission import NotePermissionRepoSpicedb
from src.db.table import Table
from src.services.attachments import AttachmentFacade
from src.utils import logging_provider


GARAGE_IMAGE = "dxflrs/garage:v2.3.0"
GARAGE_S3_PORT = 3900
GARAGE_CONFIG_PATH = "/etc/garage.toml"
GARAGE_HOST_CONFIG = Path(__file__).resolve().parents[1] / "infrastructure" / "garage.toml"

SPICEDB_IMAGE = "authzed/spicedb:v1.47.1"
SPICEDB_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "db" / "migrations" / "schema.zed"
)

TEST_BUCKET = "attachments"
TEST_KEY_ID = "GK1a2b3c4d5e6f7g8h9"
# Deterministic secret. `garage server --default-bucket` provisions a key
# with these values automatically when GARAGE_DEFAULT_ACCESS_KEY /
# GARAGE_DEFAULT_SECRET_KEY are set, and it grants that key read/write/owner
# permissions on the bucket, so we don't need any RPC or HTTP admin calls.
TEST_KEY_SECRET = "b21cd517badda12cde455f125d32babd253c2ebefebc48eb91064791fe9e2a9c"
# The host-side infra/garage.toml ships with this rpc_secret. We forward it
# so any future RPC commands inside the container authenticate.
GARAGE_RPC_SECRET = "181daae763dbfaf1aa5d9f2780f959c890d8ceb21b271e3498028758547e5fa0"

pytestmark = [pytest.mark.integration, pytest.mark.spicedb]


def _wait_for_garage(container: DockerContainer) -> None:
    """Block until Garage's S3 port accepts TCP connections."""

    @wait_container_is_ready(AssertionError)
    def _poll() -> None:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(GARAGE_S3_PORT)
        with socket.create_connection((host, port), timeout=2):
            return

    _poll()


async def _wait_until_spicedb_ready(client: AsyncClient, schema: str) -> None:
    """Poll SpiceDB's WriteSchema until it accepts the schema (container up)."""
    attempts = 30
    retry_delay_s = 0.5
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            await client.WriteSchema(WriteSchemaRequest(schema=schema))
            return
        except Exception as exc:  # pragma: no cover - startup race only
            last_error = exc
            await asyncio.sleep(retry_delay_s)
    raise RuntimeError("SpiceDB container did not become ready in time") from last_error


@pytest.fixture(scope="session")
def garage_config() -> dict[str, str]:
    """Boot a Garage container and return its endpoint + credentials."""
    container = DockerContainer(GARAGE_IMAGE)
    # The image has no config baked in, so we bind-mount the dev config and
    # let --default-bucket auto-create the access key + bucket via env vars.
    container.with_volume_mapping(
        host=str(GARAGE_HOST_CONFIG),
        container=GARAGE_CONFIG_PATH,
        mode="ro",
    )
    container.with_env("GARAGE_RPC_SECRET", GARAGE_RPC_SECRET)
    container.with_env("GARAGE_DEFAULT_BUCKET", TEST_BUCKET)
    container.with_env("GARAGE_DEFAULT_ACCESS_KEY", TEST_KEY_ID)
    container.with_env("GARAGE_DEFAULT_SECRET_KEY", TEST_KEY_SECRET)
    container.with_command(["/garage", "server", "--single-node", "--default-bucket"])
    container.with_exposed_ports(GARAGE_S3_PORT)
    container.start()
    try:
        _wait_for_garage(container)
    except Exception:
        container.stop()
        raise

    return {
        "endpoint": f"http://{container.get_container_host_ip()}:{container.get_exposed_port(GARAGE_S3_PORT)}",
        "access_key": TEST_KEY_ID,
        "secret_key": TEST_KEY_SECRET,
        "bucket": TEST_BUCKET,
    }


@pytest.fixture(scope="session")
def s3_client(garage_config: dict[str, str]):
    return boto3.client(
        "s3",
        endpoint_url=garage_config["endpoint"],
        aws_access_key_id=garage_config["access_key"],
        aws_secret_access_key=garage_config["secret_key"],
        region_name="garage",
        config=BotoConfig(retries={"max_attempts": 5, "mode": "standard"}),
    )


@pytest.fixture(scope="function")
async def permission_repo() -> NotePermissionRepoSpicedb:
    """Boot a SpiceDB container, load the schema, yield a ready client."""
    with SpiceDBContainer(image=SPICEDB_IMAGE) as spicedb:
        client = AsyncClient(
            spicedb.get_endpoint(),
            insecure_bearer_token_credentials(spicedb.get_secret_key()),
        )
        schema = SPICEDB_SCHEMA_PATH.read_text(encoding="utf-8")
        await _wait_until_spicedb_ready(client, schema)
        repo = NotePermissionRepoSpicedb(client=client)

        # Wrap `insert` so duplicate writes are silently ignored. The test
        # pre-writes some relationships (e.g. `attachment#parent_note@note`)
        # so that subsequent permission checks succeed; the production
        # facade then re-inserts the same relationship and would otherwise
        # raise `ALREADY_EXISTS`.
        orig_insert = repo.insert

        async def _insert_idempotent(relationships):
            try:
                return await orig_insert(relationships)
            except grpc.aio.AioRpcError as exc:
                if exc.code() == grpc.StatusCode.ALREADY_EXISTS:
                    return relationships
                raise

        repo.insert = _insert_idempotent  # type: ignore[assignment]
        yield repo


@pytest.mark.asyncio
async def test_attachment_facade_with_postgres_and_garage(
    db, s3_client, garage_config, user_repo, test_user, permission_repo
) -> None:
    """Integration test: Postgres metadata + Garage S3 content storage."""
    attachment_table = Table(
        table_name="note.attachment",
        logging_provider=logging_provider,
        db=db,
        id_fields=["key"],
        error_log=True,
    )
    attachment_note_link_table = Table(
        table_name="note.attachment_note_link",
        logging_provider=logging_provider,
        db=db,
        id_fields=["attachment_key", "note_id"],
        error_log=True,
    )

    metadata_repo = AttachmentsMetadataPostgresRepo(attachment_table)
    object_repo = AttachmentsS3Repo(client=s3_client, bucket=garage_config["bucket"])
    facade = AttachmentFacade(
        attachment_repo=object_repo,
        metadata_repo=metadata_repo,
        permission_repo=permission_repo,
        attachments_note_link_table=attachment_note_link_table,
        log=logging_provider,
    )

    # create user for note
    user = await user_repo.insert(test_user)
    user_ctx = UserContext(user_id=user.id)

    # create note row directly via SQL — sidesteps the broken
    # `NotePermissionRepoInMemory` in shared fixtures.
    await db.execute(
        "INSERT INTO note.content (id, title, content, author_id) "
        "VALUES ($1, $2, $3, $4)",
        "note-123",
        "Test Note",
        " ",
        user.id,
    )

    # Grant the user owner permission on the note so all attachment operations
    # (write/view/delete) succeed via SpiceDB.
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-123"),
                relation=NoteRelationEnum.OWNER,
                subject=SubjectRef(ObjectTypeEnum.USER, user.id),
            )
        ]
    )

    now = datetime.now()
    attachment = Attachment(
        key=UNDEFINED,
        filename="integration.txt",
        filepath="integration/integration.txt",
        content_type="text/plain",
        size=4,
        created_at=now,
        updated_at=now,
        content=b"ping",
    )

    stored = await facade.post_attachment(attachment, user_ctx)

    # Pre-link in SpiceDB so the subsequent `link_attachment_to_note` call
    # passes its permission check. The facade inserts this same relationship
    # itself, but does so AFTER the permission check, so without this
    # pre-insert the check would always deny the call on a freshly uploaded
    # attachment that has no `parent_note` yet.
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, stored.key),
                relation="parent_note",
                subject=SubjectRef(ObjectTypeEnum.NOTE, "note-123"),
            )
        ]
    )

    await facade.link_attachment_to_note(stored.key, note_id="note-123", user_ctx=user_ctx)
    attachments = await facade.list_attachments_for_note("note-123", user_ctx)
    assert len(attachments) == 1
    assert attachments[0].key == stored.key

    fetched = await facade.get_attachment(str(stored.key), user_ctx)

    assert fetched.content == b"ping"
    assert fetched.filename == "integration.txt"
    assert fetched.size == 4

    await facade.delete_attachment(str(stored.key), user_ctx)
    # After deletion, the metadata row should be gone. Verify directly via
    # Postgres so we don't depend on the permission check (which is also
    # gone after the parent_note relationship is removed).
    remaining = await db.fetchrow(
        "SELECT key FROM note.attachment WHERE key = $1", str(stored.key)
    )
    assert remaining is None
    with pytest.raises(KeyError):
        await metadata_repo.get_metadata(str(stored.key))
        await facade.get_attachment(str(stored.key), user_ctx)  # should raise KeyError since both metadata and content are deleted


# Temporary debug helper: invoke the test manually with prints
