from datetime import datetime

from tests.doubles import _DummyUserContext as UserContext
from src.db.repos.note.versioning import NoteVersionPostgresRepo

from tests.doubles.fakes.note_version_unit import _FakeTable
from tests.doubles.dummies.note_version_unit import _DummyTable






async def test_versioning_patch_roundtrip() -> None:
    """Ensure the patch encoding/decoding roundtrips correctly."""
    repo = NoteVersionPostgresRepo(
        snapshot_table=_DummyTable("note.version_snapshot"),
        delta_table=_DummyTable("note.version_delta"),
        max_deltas_per_snapshot=2,
    )

    old_text = "Title A\nLine 1\nLine 2\n"
    new_text = "Title B\nLine 1\nLine 2 changed\n"

    patch = repo._build_patch(old_text, new_text)
    restored = repo._apply_patch(patch, old_text)

    assert restored == new_text


async def test_versioning_uses_latest_snapshot_before_target_version() -> None:
    """Ensure the latest snapshot before the target version is used."""
    base_repo = NoteVersionPostgresRepo(
        snapshot_table=_DummyTable("note.version_snapshot"),
        delta_table=_DummyTable("note.version_delta"),
        max_deltas_per_snapshot=2,
    )

    note_id = "note-1"
    snap_1_id = "snap-1"
    snap_5_id = "snap-5"

    v1_content = "A\n"
    v2_content = "B\n"
    v3_content = "C\n"
    v4_content = "D\n"
    v5_content = "E\n"
    v6_content = "F\n"

    snapshots = [
        {
            "snapshot_id": snap_1_id,
            "note_id": note_id,
            "version_index": 1,
            "created_at": datetime(2026, 5, 18, 9, 0, 0),
            "author_id": "user-1",
            "title": "v1",
            "content": v1_content,
        },
        {
            "snapshot_id": snap_5_id,
            "note_id": note_id,
            "version_index": 5,
            "created_at": datetime(2026, 5, 18, 9, 20, 0),
            "author_id": "user-1",
            "title": "v5",
            "content": v5_content,
        },
    ]

    deltas = [
        {
            "delta_id": "d2",
            "note_id": note_id,
            "snapshot_id": snap_1_id,
            "version_index": 2,
            "created_at": datetime(2026, 5, 18, 9, 5, 0),
            "author_id": "user-1",
            "title_patch": base_repo._build_patch("v1", "v2"),
            "content_patch": base_repo._build_patch(v1_content, v2_content),
        },
        {
            "delta_id": "d3",
            "note_id": note_id,
            "snapshot_id": snap_1_id,
            "version_index": 3,
            "created_at": datetime(2026, 5, 18, 9, 10, 0),
            "author_id": "user-1",
            "title_patch": base_repo._build_patch("v2", "v3"),
            "content_patch": base_repo._build_patch(v2_content, v3_content),
        },
        {
            "delta_id": "d4",
            "note_id": note_id,
            "snapshot_id": snap_1_id,
            "version_index": 4,
            "created_at": datetime(2026, 5, 18, 9, 15, 0),
            "author_id": "user-1",
            "title_patch": base_repo._build_patch("v3", "v4"),
            "content_patch": base_repo._build_patch(v3_content, v4_content),
        },
        {
            "delta_id": "d6",
            "note_id": note_id,
            "snapshot_id": snap_5_id,
            "version_index": 6,
            "created_at": datetime(2026, 5, 18, 9, 25, 0),
            "author_id": "user-1",
            "title_patch": base_repo._build_patch("v5", "v6"),
            "content_patch": base_repo._build_patch(v5_content, v6_content),
        },
    ]

    repo = NoteVersionPostgresRepo(
        snapshot_table=_FakeTable("note.version_snapshot", snapshots),
        delta_table=_FakeTable("note.version_delta", deltas),
        max_deltas_per_snapshot=2,
    )

    restored = await repo.get_content_at_version(note_id, 6)
    assert restored.title == "v6"
    assert restored.content == v6_content


async def test_versioning_uses_snapshot_at_target_version() -> None:
    """Ensure exact snapshot version does not apply unrelated deltas."""
    note_id = "note-1"
    snap_3_id = "snap-3"
    snapshots = [
        {
            "snapshot_id": snap_3_id,
            "note_id": note_id,
            "version_index": 3,
            "created_at": datetime(2026, 5, 18, 9, 10, 0),
            "author_id": "user-1",
            "title": "v3",
            "content": "C\n",
        }
    ]
    deltas = [
        {
            "delta_id": "d4",
            "note_id": note_id,
            "snapshot_id": snap_3_id,
            "version_index": 4,
            "created_at": datetime(2026, 5, 18, 9, 15, 0),
            "author_id": "user-1",
            "title_patch": "",
            "content_patch": "",
        }
    ]

    repo = NoteVersionPostgresRepo(
        snapshot_table=_FakeTable("note.version_snapshot", snapshots),
        delta_table=_FakeTable("note.version_delta", deltas),
        max_deltas_per_snapshot=2,
    )

    restored = await repo.get_content_at_version(note_id, 3)
    assert restored.title == "v3"
    assert restored.content == "C\n"
