from datetime import datetime, timezone
from pathlib import Path

import boto3
import pytest
from dotenv import dotenv_values

from src.api.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.attachments.attachments import (
    Attachment,
    AttachmentsMetadataPostgresRepo,
    AttachmentsS3Repo,
)
from src.db.repos.note.note import UserContext
from src.db.table import Table
from src.grpc_mod.proto.note_pb2 import Note
from src.services.attachments import AttachmentFacade
from src.utils import logging_provider

GARAGE_ENV_FILE = Path("infrastructure/.garage.env")
GARAGE_ENDPOINT = "http://localhost:3900"

pytestmark = [pytest.mark.integration]


def load_garage_config() -> dict[str, str]:
    if not GARAGE_ENV_FILE.exists():
        raise RuntimeError(f"Garage environment file not found: {GARAGE_ENV_FILE}")

    config = dotenv_values(GARAGE_ENV_FILE)
    required = (
        "GARAGE_DEFAULT_ACCESS_KEY",
        "GARAGE_DEFAULT_SECRET_KEY",
        "GARAGE_DEFAULT_BUCKET",
    )
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise RuntimeError(f"Missing Garage configuration values: {', '.join(missing)}")

    return {
        "access_key": config["GARAGE_DEFAULT_ACCESS_KEY"],
        "secret_key": config["GARAGE_DEFAULT_SECRET_KEY"],
        "bucket": config["GARAGE_DEFAULT_BUCKET"],
    }


@pytest.fixture(scope="session")
def garage_config() -> dict[str, str]:
    return load_garage_config()


@pytest.fixture(scope="session")
def s3_client(garage_config):
    return boto3.client(
        "s3",
        endpoint_url=GARAGE_ENDPOINT,
        aws_access_key_id=garage_config["access_key"],
        aws_secret_access_key=garage_config["secret_key"],
        region_name="garage",
    )


@pytest.mark.asyncio
async def test_attachment_facade_with_postgres_and_garage(db, s3_client, garage_config, note_repo_facade, user_repo, test_user) -> None:
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
        attachments_note_link_table=attachment_note_link_table,
        log=logging_provider,
    )

    now = datetime.now(timezone.utc).isoformat()
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

    stored = await facade.post_attachment(attachment)

    # create user for note
    user = await user_repo.insert(test_user)
    # create note for linking
    await note_repo_facade.insert(
        NoteEntity("note-123", "Test Note", content=" "),
        user=UserContext(user_id=user.id)
    )


    await facade.link_attachment_to_note(stored.key, note_id="note-123")
    attachments =await facade.list_attachments_for_note("note-123")
    assert len(attachments) == 1
    assert attachments[0].key == stored.key

    fetched = await facade.get_attachment(str(stored.key))

    assert fetched.content == b"ping"
    assert fetched.filename == "integration.txt"
    assert fetched.size == 4

    await facade.delete_attachment(str(stored.key))
    with pytest.raises(KeyError):
        await facade.get_attachment(str(stored.key))  # should raise KeyError since both metadata and content are deleted
