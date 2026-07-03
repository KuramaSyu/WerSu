from datetime import datetime

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.versioning import NoteVersionPostgresRepo
from src.db.table import Table
from src.utils import logging_provider

from tests.fixtures import db, dsn, user_repo, test_user

pytestmark = pytest.mark.integration


async def test_versioning_snapshot_delta_rotation(db, user_repo, test_user):
    """Ensure snapshots rotate after the configured delta threshold."""
    user = await user_repo.insert(test_user)

    common_table_kwargs = {"db": db, "logging_provider": logging_provider}
    content_table = Table(
        **common_table_kwargs,
        table_name="note.content",
        id_fields=["id"],
        error_log=True,
    )
    snapshot_table = Table(
        **common_table_kwargs,
        table_name="note.version_snapshot",
        id_fields=["snapshot_id"],
        error_log=True,
    )
    delta_table = Table(
        **common_table_kwargs,
        table_name="note.version_delta",
        id_fields=["delta_id"],
        error_log=True,
    )

    content_repo = NoteContentPostgresRepo(content_table)
    version_repo = NoteVersionPostgresRepo(
        snapshot_table=snapshot_table,
        delta_table=delta_table,
        max_deltas_per_snapshot=2,
    )

    note = await content_repo.insert(
        NoteEntity(
            title="v1",
            content="alpha",
            updated_at=datetime(2026, 5, 18, 10, 0, 0),
            author_id=user.id,
        )
    )
    assert note.note_id is not None

    await version_repo.record_initial_snapshot(
        note_id=note.note_id,
        title=note.title,
        content=note.content,
        author_id=user.id,
        created_at=datetime(2026, 5, 18, 10, 0, 0),
    )

    await version_repo.append_version(
        note_id=note.note_id,
        old_title="v1",
        old_content="alpha",
        new_title="v2",
        new_content="bravo",
        author_id=user.id,
        created_at=datetime(2026, 5, 18, 10, 5, 0),
    )
    await version_repo.append_version(
        note_id=note.note_id,
        old_title="v2",
        old_content="bravo",
        new_title="v3",
        new_content="charlie",
        author_id=user.id,
        created_at=datetime(2026, 5, 18, 10, 10, 0),
    )
    await version_repo.append_version(
        note_id=note.note_id,
        old_title="v3",
        old_content="charlie",
        new_title="v4",
        new_content="delta",
        author_id=user.id,
        created_at=datetime(2026, 5, 18, 10, 15, 0),
    )

    versions = await version_repo.list_versions(note.note_id, limit=10, offset=0)
    assert len(versions) == 4
    assert versions[0].is_snapshot is True
    assert versions[0].version_index == 4

    v2 = await version_repo.get_content_at_version(note.note_id, 2)
    assert v2.title == "v2"
    assert v2.content == "bravo"

    v3 = await version_repo.get_content_at_version(note.note_id, 3)
    assert v3.title == "v3"
    assert v3.content == "charlie"

    v4 = await version_repo.get_content_at_version(note.note_id, 4)
    assert v4.title == "v4"
    assert v4.content == "delta"
