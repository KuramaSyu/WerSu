"""Integration tests for the attachment facade with real Postgres + SpiceDB + Garage.

Boilerplate (Garage container, SpiceDB container, idempotent
``permission_repo`` insert wrapper) now lives in:

* :mod:`tests.fixtures.garage` -> ``garage_config``, ``s3_client``
* :mod:`tests.fixtures.spicedb` -> ``idempotent_permission_repo``

The test body focuses on round-tripping a single attachment through
``AttachmentFacade`` and verifying state is gone after the delete
flow.
"""

from datetime import datetime

import pytest

from tests.stubs.user_context import _UserContext as UserContext
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
from src.db.table import Table
from src.services.attachments import AttachmentFacade
from src.utils import logging_provider


pytestmark = [pytest.mark.implementation, pytest.mark.spicedb]


# Local alias kept for compatibility with the test signature below.
@pytest.fixture(scope="function")
async def permission_repo(
    idempotent_permission_repo,
):
    """Alias around the canonical ``idempotent_permission_repo`` fixture."""
    return idempotent_permission_repo


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
