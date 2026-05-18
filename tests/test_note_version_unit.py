from datetime import datetime

from src.db.repos.note.versioning import NoteVersionPostgresRepo


class _FakeTable:
    """In-memory table stub to unit-test version selection logic."""

    def __init__(self, name: str, rows: list[dict]) -> None:
        self.name = name
        self._rows = rows

    async def fetch(self, sql: str, *args):
        if "FROM note.version_snapshot" in sql and "version_index <=" in sql:
            note_id, version_index = args
            matches = [
                row
                for row in self._rows
                if row["note_id"] == note_id and row["version_index"] <= version_index
            ]
            matches.sort(key=lambda row: row["version_index"], reverse=True)
            return matches[:1]

        if "FROM note.version_delta" in sql and "version_index >" in sql:
            note_id, start_version, end_version = args
            matches = [
                row
                for row in self._rows
                if row["note_id"] == note_id
                and row["version_index"] > start_version
                and row["version_index"] <= end_version
            ]
            matches.sort(key=lambda row: row["version_index"])
            return matches

        if "FROM note.version_snapshot" in sql and "ORDER BY version_index DESC" in sql:
            note_id = args[0]
            matches = [row for row in self._rows if row["note_id"] == note_id]
            matches.sort(key=lambda row: row["version_index"], reverse=True)
            return matches[:1]

        if "FROM note.version_delta" in sql and "COUNT" in sql:
            note_id, snapshot_id = args
            matches = [
                row
                for row in self._rows
                if row["note_id"] == note_id and row["snapshot_id"] == snapshot_id
            ]
            return [{"delta_count": len(matches)}]

        return []


class _DummyTable:
    """Minimal table placeholder for unit testing diff logic."""

    def __init__(self, name: str) -> None:
        self.name = name


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
