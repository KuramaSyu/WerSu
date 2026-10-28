"""Unit tests for :class:`src.services.attachment_facade.AttachmentFacadeImpl`.

Focus of this module is the on-demand per-attachment JWT flow used by
live collaboration: a peer that can view a note an attachment is
linked to must be able to mint a token for that attachment, while a
peer with no relationship to the attachment must be declined.
"""

from datetime import datetime

import pytest

from tests._fixtures_pkg.fakes import _FakeJwtProvider
from tests.stubs.attachments import (
    InMemoryAttachmentMetadataRepo,
    InMemoryAttachmentRepo,
)
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.recording_table import RecordingTable
from tests.stubs.user_context import _UserContext as UserContext
from src.api.other.relationship import (
    AttachmentRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.other.undefined import UNDEFINED
from src.db.repos.attachments.attachments import Attachment
from src.services.attachment_facade import AttachmentFacadeImpl
from src.utils import logging_provider


def _make_facade(permission_repo: InMemoryPermissionRepo) -> AttachmentFacadeImpl:
    return AttachmentFacadeImpl(
        attachment_repo=InMemoryAttachmentRepo(),
        metadata_repo=InMemoryAttachmentMetadataRepo(),
        permission_repo=permission_repo,
        attachments_note_link_table=RecordingTable(name="note_link"),
        attachments_user_link_table=RecordingTable(name="user_link"),
        log=logging_provider,
        jwt_provider=_FakeJwtProvider(),
    )


async def _link_attachment_to_note(
    facade: AttachmentFacadeImpl,
    permission_repo: InMemoryPermissionRepo,
    *,
    attachment_key: str,
    note_id: str,
    owner_id: str,
) -> None:
    """Link *attachment_key* to *note_id* as *owner_id*.

    Mirrors how a note gets an embedded attachment in production:
    the uploader already holds ``parent_user`` on the attachment plus
    write access to the note, then writes the ``parent_note`` link.
    """
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                relation=AttachmentRelationEnum.PARENT_USER,
                subject=SubjectRef(ObjectTypeEnum.USER, owner_id),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
                relation=NoteRelationEnum.OWNER,
                subject=SubjectRef(ObjectTypeEnum.USER, owner_id),
            ),
        ]
    )
    await facade.link_attachment(
        attachment_key=attachment_key,
        sub_type="note",
        sub_id=note_id,
        user_ctx=UserContext(user_id=owner_id),
    )


async def test_get_attachment_token_declines_when_not_linked_to_a_note() -> None:
    """No note link -> no view permission -> ``PermissionError``."""
    permission_repo = InMemoryPermissionRepo()
    facade = _make_facade(permission_repo)

    with pytest.raises(PermissionError):
        await facade.get_attachment_token("att-orphan", UserContext(user_id="bob"))


async def test_get_attachment_token_accepts_when_linked_to_a_note() -> None:
    """A user who can view the parent note can mint the attachment token."""
    permission_repo = InMemoryPermissionRepo()
    facade = _make_facade(permission_repo)

    await _link_attachment_to_note(
        facade,
        permission_repo,
        attachment_key="att-1",
        note_id="note-1",
        owner_id="alice",
    )
    # Bob gains access purely through the note link.
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
                relation=NoteRelationEnum.READER,
                subject=SubjectRef(ObjectTypeEnum.USER, "bob"),
            )
        ]
    )

    token = await facade.get_attachment_token("att-1", UserContext(user_id="bob"))
    assert token == "jwt:bob:att-1"


async def test_get_attachment_token_declines_linked_note_the_user_cannot_view() -> None:
    """Linked to a note, but the user cannot view that note -> declined."""
    permission_repo = InMemoryPermissionRepo()
    facade = _make_facade(permission_repo)

    await _link_attachment_to_note(
        facade,
        permission_repo,
        attachment_key="att-1",
        note_id="note-1",
        owner_id="alice",
    )

    with pytest.raises(PermissionError):
        await facade.get_attachment_token("att-1", UserContext(user_id="mallory"))


async def test_attachment_facade_round_trip() -> None:
    """Upload -> read -> delete round trip, with permissions granted."""
    attachment_repo = InMemoryAttachmentRepo()
    metadata_repo = InMemoryAttachmentMetadataRepo()
    permission_repo = InMemoryPermissionRepo()
    facade = AttachmentFacadeImpl(
        attachment_repo=attachment_repo,
        metadata_repo=metadata_repo,
        permission_repo=permission_repo,
        attachments_note_link_table=RecordingTable(name="note_link"),
        attachments_user_link_table=RecordingTable(name="user_link"),
        log=logging_provider,
        jwt_provider=_FakeJwtProvider(),
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

    stored = await facade.post_attachment(attachment, UserContext(user_id="alice"))
    assert stored.key is not UNDEFINED
    key = str(stored.key)

    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, key),
                relation=AttachmentRelationEnum.PARENT_USER,
                subject=SubjectRef(ObjectTypeEnum.USER, "alice"),
            )
        ]
    )

    fetched = await facade.get_attachment(key, UserContext(user_id="alice"))
    assert fetched.content == b"hello"
    assert fetched.filename == "hello.txt"

    await facade.delete_attachment(key, UserContext(user_id="alice"))
    # The delete removed both the permission tuple and the metadata.
    with pytest.raises(PermissionError):
        await facade.get_metadata(key, UserContext(user_id="alice"))
    with pytest.raises(KeyError):
        await metadata_repo.get_metadata(key)
