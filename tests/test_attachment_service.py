from datetime import datetime

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from src.api.other.undefined import UNDEFINED
from src.db.repos.attachments.attachments import Attachment
from src.services.attachment_facade import AttachmentFacadeImpl
from src.utils import logging_provider
from tests.stubs.attachments import InMemoryAttachmentRepo, InMemoryAttachmentMetadataRepo


@pytest.mark.asyncio
async def _test_attachment_facade_round_trip() -> None:
    """Validate upload -> read -> delete for attachment facade with in-memory stubs."""
    # ISSUE: needs a stub table for links
    attachment_repo = InMemoryAttachmentRepo()
    metadata_repo = InMemoryAttachmentMetadataRepo()
    facade = AttachmentFacadeImpl(
        attachment_repo=attachment_repo,
        metadata_repo=metadata_repo,
        log=logging_provider,
    )

    now = datetime.utcnow().isoformat()
    attachment = Attachment(
        key=UNDEFINED,
        filename="hello.txt",
        filepath="uploads/hello.txt",
        content_type="text/plain",
        size=5,
        created_at=now,
        updated_at=now,
        content=b"hello",
    )

    stored = await facade.post_attachment(attachment)
    assert stored.key is not UNDEFINED

    fetched = await facade.get_attachment(str(stored.key))
    assert fetched.content == b"hello"
    assert fetched.filename == "hello.txt"

    await facade.delete_attachment(str(stored.key))
    with pytest.raises(KeyError):
        await facade.get_metadata(str(stored.key))
