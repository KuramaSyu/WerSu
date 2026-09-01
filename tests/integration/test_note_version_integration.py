from dataclasses import replace
from datetime import datetime
from src.api.other.relationship import DirectoryRelationEnum, ObjectRef, ObjectTypeEnum, Relationship, SubjectRef
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.note.combined import CombinedNotePostgresRepo
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.note_facade import NoteFacadeImpl
from src.db.repos.note.versioning import NoteVersionPostgresRepo
from src.db.repos.tag.postgres import PostgresTagRepo
from src.db.table import Table
from src.utils import logging_provider
from tests.fixtures import _FakeEmbeddingRepo, _TestDirectoryRepo, db, dsn, test_user, user_repo
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.in_memory_rule_repo import InMemoryRuleRepo
from tests.stubs.in_memory_shelf_repo import NoopShelfRepo
from tests.stubs.user_context import _UserContext as UserContext
from typing import List
import pytest

pytestmark = pytest.mark.integration


async def test_note_versioning_records_snapshots_and_deltas(db, user_repo, test_user) -> None:
    """Integration test: insert/update note and validate version history."""
    user = await user_repo.insert(test_user)
    ctx = UserContext(user_id=user.id)

    common_table_kwargs = {"db": db, "logging_provider": logging_provider}
    content_table = Table(
        **common_table_kwargs,
        table_name="note.content",
        id_fields=["id"],
        error_log=True,
    )
    version_snapshot_table = Table(
        **common_table_kwargs,
        table_name="note.version_snapshot",
        id_fields=["snapshot_id"],
        error_log=True,
    )
    version_delta_table = Table(
        **common_table_kwargs,
        table_name="note.version_delta",
        id_fields=["delta_id"],
        error_log=True,
    )

    version_repo = NoteVersionPostgresRepo(
        snapshot_table=version_snapshot_table,
        delta_table=version_delta_table,
        max_deltas_per_snapshot=1,
    )

    note_tags_table = Table(
        **common_table_kwargs,
        table_name="note.note_tag",
        id_fields=["note_id", "tag_id"],
        error_log=True,
    )
    tag_repo = PostgresTagRepo(
        tag_table=Table(
            **common_table_kwargs,
            table_name="note.tag",
            id_fields=["id"],
            error_log=True,
        ),
        note_tag_table=note_tags_table,
        directory_tag_table=Table(
            **common_table_kwargs,
            table_name="note.directory_tag",
            id_fields=["directory_id", "tag_id"],
            error_log=True,
        ),
        db=db,
    )

    directory_repo = _TestDirectoryRepo()
    note_repo = NoteFacadeImpl(
        db=db,
        content_repo=NoteContentPostgresRepo(content_table),
        combined_repo=CombinedNotePostgresRepo(db=db),
        embedding_repo=_FakeEmbeddingRepo(),
        permission_repo=InMemoryPermissionRepo(),
        directory_repo=directory_repo,
        tag_repo=tag_repo,
        logging_provider=logging_provider,
        version_repo=version_repo,
        shelf_repo=NoopShelfRepo(),
        rule_repo=InMemoryRuleRepo(),
    )

    # Seed a default directory the user owns so the note facade's
    # ``_resolve_directory_ids`` finds a parent.  The
    # :class:`_TestDirectoryRepo` matches via
    # ``user_to_directory_ids``; we register an explicit
    # ``directory#admin@user`` relation so the lookup returns the
    # id we put on the note.
    default_dir_id = "dir-default"
    directory_repo.user_to_directory_ids[str(user.id)] = [default_dir_id]
    await note_repo._permission_repo.insert([
        Relationship(
            resource=ObjectRef(
                object_type=ObjectTypeEnum.DIRECTORY,
                object_id=default_dir_id,
            ),
            relation=DirectoryRelationEnum.ADMIN,
            subject=SubjectRef(
                object_type=ObjectTypeEnum.USER,
                object_id=str(user.id),
            ),
        )
    ])

    base_note = NoteEntity(
        title="v1",
        content="alpha",
        updated_at=datetime(2026, 5, 18, 11, 0, 0),
        author_id=user.id,
        directory_ids=[default_dir_id],
    )
    created = await note_repo.insert(base_note, ctx)

    updated_v2 = replace(
        created,
        title="v2",
        content="bravo",
        updated_at=datetime(2026, 5, 18, 11, 5, 0),
    )
    await note_repo.update(updated_v2, ctx)

    updated_v3 = replace(
        created,
        title="v3",
        content="charlie",
        updated_at=datetime(2026, 5, 18, 11, 10, 0),
    )
    await note_repo.update(updated_v3, ctx)

    versions = await version_repo.list_versions(created.note_id, limit=10, offset=0)
    assert len(versions) == 3
    assert versions[0].is_snapshot is True
    assert versions[0].version_index == 3

    restored_v2 = await version_repo.get_content_at_version(created.note_id, 2)
    assert restored_v2.title == "v2"
    assert restored_v2.content == "bravo"
