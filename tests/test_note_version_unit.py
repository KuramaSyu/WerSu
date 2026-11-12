import json
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


def _repo_with_snapshot_and_edit(
    *,
    old_text: str,
    new_text: str,
    old_title: str = "Title",
    new_title: str = "Title",
) -> NoteVersionPostgresRepo:
    """Build a repo with version 1 = ``old_text`` and version 2 = ``new_text``."""
    builder = NoteVersionPostgresRepo(
        snapshot_table=_DummyTable("note.version_snapshot"),
        delta_table=_DummyTable("note.version_delta"),
        max_deltas_per_snapshot=100,
    )
    note_id = "note-1"
    snap_id = "snap-1"
    snapshots = [
        {
            "snapshot_id": snap_id,
            "note_id": note_id,
            "version_index": 1,
            "created_at": datetime(2026, 5, 18, 9, 0, 0),
            "author_id": "user-1",
            "title": old_title,
            "content": old_text,
        }
    ]
    deltas = [
        {
            "delta_id": "d2",
            "note_id": note_id,
            "snapshot_id": snap_id,
            "version_index": 2,
            "created_at": datetime(2026, 5, 18, 9, 5, 0),
            "author_id": "user-1",
            "title_patch": builder._build_patch(old_title, new_title),
            "content_patch": builder._build_patch(old_text, new_text),
        }
    ]
    return NoteVersionPostgresRepo(
        snapshot_table=_FakeTable("note.version_snapshot", snapshots),
        delta_table=_FakeTable("note.version_delta", deltas),
        max_deltas_per_snapshot=100,
    )


async def test_versioning_restores_a_version_that_deletes_content() -> None:
    """Deleting a block must not drop the surrounding untouched content."""
    old_text = (
        "# Notes\n"
        "\n"
        "first paragraph\n"
        "second paragraph\n"
        "\n"
        "obsolete paragraph\n"
        "\n"
        "third paragraph\n"
        "fourth paragraph\n"
    )
    new_text = old_text.replace("obsolete paragraph\n\n", "")

    repo = _repo_with_snapshot_and_edit(old_text=old_text, new_text=new_text)

    restored = await repo.get_content_at_version("note-1", 2)

    assert restored.content == new_text
    assert "# Notes" in restored.content
    assert "first paragraph" in restored.content
    assert "fourth paragraph" in restored.content
    assert "obsolete paragraph" not in restored.content


async def test_versioning_restores_a_version_that_deletes_table_rows_and_cells() -> None:
    """Table edits are line-based patches too; rows outside the hunk survive."""
    old_text = (
        "# Data\n"
        "\n"
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| alpha | 1 |\n"
        "| beta | 2 |\n"
        "| gamma | 3 |\n"
        "| delta | 4 |\n"
        "| epsilon | 5 |\n"
        "\n"
        "footer\n"
    )
    new_text = (
        "# Data\n"
        "\n"
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| alpha | 1 |\n"
        "| beta | 20 |\n"
        "| delta | 4 |\n"
        "| epsilon | 5 |\n"
        "\n"
        "footer\n"
    )

    repo = _repo_with_snapshot_and_edit(old_text=old_text, new_text=new_text)

    restored = await repo.get_content_at_version("note-1", 2)

    assert restored.content == new_text
    assert "# Data" in restored.content
    assert "| Name | Value |" in restored.content
    assert "| beta | 20 |" in restored.content
    assert "| alpha | 1 |" in restored.content
    assert "| gamma | 3 |" not in restored.content
    assert restored.content.endswith("footer\n")


async def test_versioning_restores_a_version_that_changes_bullet_indentation() -> None:
    """Re-indenting nested bullets must keep the rest of the list intact."""
    old_text = (
        "# Groceries\n"
        "\n"
        "intro line one\n"
        "intro line two\n"
        "\n"
        "- fruit\n"
        "  - apple\n"
        "  - banana\n"
        "- vegetables\n"
        "  - carrot\n"
        "  - potato\n"
        "\n"
        "outro line one\n"
        "outro line two\n"
    )
    new_text = (
        "# Groceries\n"
        "\n"
        "intro line one\n"
        "intro line two\n"
        "\n"
        "- fruit\n"
        "    - apple\n"
        "    - banana\n"
        "- vegetables\n"
        "    - carrot\n"
        "    - potato\n"
        "\n"
        "outro line one\n"
        "outro line two\n"
    )

    repo = _repo_with_snapshot_and_edit(old_text=old_text, new_text=new_text)

    restored = await repo.get_content_at_version("note-1", 2)

    assert restored.content == new_text
    assert "# Groceries" in restored.content
    assert "    - apple" in restored.content
    assert "    - potato" in restored.content
    assert "intro line two" in restored.content
    assert "outro line two" in restored.content


async def test_versioning_restores_multiple_separated_hunks() -> None:
    """Two edits in distant regions must both apply and preserve the middle."""
    old_text = (
        "# Title\n"
        "\n"
        "top line one\n"
        "top line two\n"
        "top line three\n"
        "\n"
        "middle one\n"
        "middle two\n"
        "middle three\n"
        "middle four\n"
        "\n"
        "bottom line one\n"
        "bottom line two\n"
        "bottom line three\n"
    )
    new_text = (
        "# Title\n"
        "\n"
        "top line one\n"
        "top line TWO changed\n"
        "top line three\n"
        "\n"
        "middle one\n"
        "middle two\n"
        "middle three\n"
        "middle four\n"
        "\n"
        "bottom line one\n"
        "bottom line two\n"
        "bottom line THREE changed\n"
    )

    repo = _repo_with_snapshot_and_edit(old_text=old_text, new_text=new_text)

    restored = await repo.get_content_at_version("note-1", 2)

    assert restored.content == new_text
    assert "top line TWO changed" in restored.content
    assert "bottom line THREE changed" in restored.content
    # The untouched region between the two hunks must not be lost.
    assert "middle one" in restored.content
    assert "middle four" in restored.content


async def test_versioning_restores_each_version_in_a_list_edit_chain() -> None:
    """Walk the exact shape that broke restore: v1 has no list, later
    versions rewrite bullets/indentation with deltas whose hunks only
    cover part of the document."""
    note_id = "note-1"
    snap_id = "snap-1"
    versions = [
        "0\n",
        "- 0\n- 1\n",
        "- 0\n- 1\n- 2\n- 2\n- 2\n- \n",
        "- 0\n- 1\n- 2\n- 2\n- 2\n- \n",  # v4: title-only save
        "- 0\n- 1\n- 2\n  - 2,4\n- 3,4\n- 2\n  - 3\n",
    ]
    titles = ["test", "test", "test", "test renamed", "test renamed"]

    builder = NoteVersionPostgresRepo(
        snapshot_table=_DummyTable("note.version_snapshot"),
        delta_table=_DummyTable("note.version_delta"),
        max_deltas_per_snapshot=100,
    )
    snapshots = [
        {
            "snapshot_id": snap_id,
            "note_id": note_id,
            "version_index": 1,
            "created_at": datetime(2026, 5, 18, 9, 0, 0),
            "author_id": "user-1",
            "title": titles[0],
            "content": versions[0],
        }
    ]
    deltas = []
    for index in range(1, len(versions)):
        version_index = index + 1
        deltas.append(
            {
                "delta_id": f"d{version_index}",
                "note_id": note_id,
                "snapshot_id": snap_id,
                "version_index": version_index,
                "created_at": datetime(2026, 5, 18, 9, index, 0),
                "author_id": "user-1",
                "title_patch": builder._build_patch(
                    titles[index - 1], titles[index]
                ),
                "content_patch": builder._build_patch(
                    versions[index - 1], versions[index]
                ),
            }
        )

    repo = NoteVersionPostgresRepo(
        snapshot_table=_FakeTable("note.version_snapshot", snapshots),
        delta_table=_FakeTable("note.version_delta", deltas),
        max_deltas_per_snapshot=100,
    )

    for index, expected_content in enumerate(versions, start=1):
        restored = await repo.get_content_at_version(note_id, index)
        assert restored.content == expected_content, (
            f"version {index} did not reconstruct\n"
            f"expected: {expected_content!r}\n"
            f"actual:   {restored.content!r}"
        )


def _compaction_repo() -> NoteVersionPostgresRepo:
    """Three deltas on day 1, one on day 2, on top of a snapshot."""
    note_id = "note-1"
    snap_id = "snap-1"
    day_one = datetime(2026, 5, 18, 9, 0, 0)
    day_two = datetime(2026, 5, 19, 9, 0, 0)

    contents = {
        1: "start\n",
        2: "start\nsecond\n",
        3: "start\nsecond\nthird\n",
        4: "start\nsecond\nthird\nfourth\n",
        5: "start\nsecond\nthird\nfourth\nfifth\n",
    }
    builder = NoteVersionPostgresRepo(
        snapshot_table=_DummyTable("note.version_snapshot"),
        delta_table=_DummyTable("note.version_delta"),
        max_deltas_per_snapshot=100,
    )
    snapshots = [
        {
            "snapshot_id": snap_id,
            "note_id": note_id,
            "version_index": 1,
            "created_at": day_one,
            "author_id": "user-1",
            "title": "start",
            "content": contents[1],
        }
    ]
    created = {2: day_one, 3: day_one, 4: day_one, 5: day_two}
    deltas = [
        {
            "delta_id": f"d{index}",
            "note_id": note_id,
            "snapshot_id": snap_id,
            "version_index": index,
            "created_at": created[index],
            "author_id": "user-1",
            "title_patch": builder._build_patch("start", "start"),
            "content_patch": builder._build_patch(
                contents[index - 1], contents[index]
            ),
        }
        for index in (2, 3, 4, 5)
    ]
    return NoteVersionPostgresRepo(
        snapshot_table=_FakeTable("note.version_snapshot", snapshots),
        delta_table=_FakeTable("note.version_delta", deltas),
        max_deltas_per_snapshot=100,
    )


async def test_versioning_compaction_merges_day_into_one_snapshot() -> None:
    repo = _compaction_repo()

    result = await repo.compact_deltas_before(datetime(2026, 5, 19))

    assert result.notes_compacted == 1
    assert result.deltas_removed == 3
    assert result.snapshots_removed == 0

    # Day 1 collapses to its snapshot; day 2's delta is untouched.
    remaining = await repo._delta_table.fetch("SELECT * FROM note.version_delta")
    assert sorted(row["version_index"] for row in remaining) == [5]

    # The retained day-1 snapshot moved to the day's last index and
    # carries the day's final state.
    restored_day_one = await repo.get_content_at_version("note-1", 4)
    assert restored_day_one.content == "start\nsecond\nthird\nfourth\n"

    # The next day's delta still applies on top of the compacted day.
    restored_day_two = await repo.get_content_at_version("note-1", 5)
    assert restored_day_two.content == (
        "start\nsecond\nthird\nfourth\nfifth\n"
    )


async def test_versioning_compaction_is_idempotent() -> None:
    repo = _compaction_repo()
    cutoff = datetime(2026, 5, 19)

    first = await repo.compact_deltas_before(cutoff)
    second = await repo.compact_deltas_before(cutoff)

    assert first.deltas_removed == 3
    assert second.deltas_removed == 0
    assert second.snapshots_removed == 0
    assert second.notes_compacted == 0


async def test_versioning_compaction_combines_many_snapshots() -> None:
    """Thirty snapshots in one day collapse to a single snapshot."""
    note_id = "note-1"
    day = datetime(2026, 5, 18, 9, 0, 0)
    snapshots = [
        {
            "snapshot_id": f"snap-{index}",
            "note_id": note_id,
            "version_index": index,
            "created_at": day,
            "author_id": "user-1",
            "title": "t",
            "content": f"content-{index}\n",
        }
        for index in range(1, 31)
    ]
    repo = NoteVersionPostgresRepo(
        snapshot_table=_FakeTable(
            "note.version_snapshot", snapshots, id_field="snapshot_id"
        ),
        delta_table=_FakeTable("note.version_delta", []),
        max_deltas_per_snapshot=100,
    )

    result = await repo.compact_deltas_before(datetime(2026, 5, 19))

    assert result.notes_compacted == 1
    assert result.snapshots_removed == 29
    remaining = await repo._snapshot_table.fetch(
        "SELECT * FROM note.version_snapshot"
    )
    assert len(remaining) == 1
    assert remaining[0]["snapshot_id"] == "snap-30"
    restored = await repo.get_content_at_version(note_id, 30)
    assert restored.content == "content-30\n"


async def test_versioning_compaction_combines_snapshots_and_deltas() -> None:
    """Three snapshots and thirty deltas in one day collapse to one snapshot."""
    note_id = "note-1"
    day = datetime(2026, 5, 18, 9, 0, 0)
    contents = {index: f"v{index}\n" for index in range(1, 35)}
    builder = NoteVersionPostgresRepo(
        snapshot_table=_DummyTable("note.version_snapshot"),
        delta_table=_DummyTable("note.version_delta"),
        max_deltas_per_snapshot=100,
    )
    snapshots = [
        {
            "snapshot_id": f"snap-{index}",
            "note_id": note_id,
            "version_index": index,
            "created_at": day,
            "author_id": "user-1",
            "title": "t",
            "content": contents[index],
        }
        for index in (1, 12, 23)
    ]
    deltas = []
    for start, snap_id in ((2, "snap-1"), (13, "snap-12"), (24, "snap-23")):
        for index in range(start, start + 10):
            deltas.append(
                {
                    "delta_id": f"d{index}",
                    "note_id": note_id,
                    "snapshot_id": snap_id,
                    "version_index": index,
                    "created_at": day,
                    "author_id": "user-1",
                    "title_patch": "",
                    "content_patch": builder._build_patch(
                        contents[index - 1], contents[index]
                    ),
                }
            )
    # A later day's delta, to prove the collapsed snapshot becomes its base.
    deltas.append(
        {
            "delta_id": "d34",
            "note_id": note_id,
            "snapshot_id": "snap-23",
            "version_index": 34,
            "created_at": datetime(2026, 5, 19, 9, 0, 0),
            "author_id": "user-1",
            "title_patch": "",
            "content_patch": builder._build_patch(contents[33], contents[34]),
        }
    )
    repo = NoteVersionPostgresRepo(
        snapshot_table=_FakeTable(
            "note.version_snapshot", snapshots, id_field="snapshot_id"
        ),
        delta_table=_FakeTable("note.version_delta", deltas),
        max_deltas_per_snapshot=100,
    )

    result = await repo.compact_deltas_before(datetime(2026, 5, 19))

    assert result.notes_compacted == 1
    assert result.deltas_removed == 30
    assert result.snapshots_removed == 2
    remaining_snapshots = await repo._snapshot_table.fetch(
        "SELECT * FROM note.version_snapshot"
    )
    assert [row["snapshot_id"] for row in remaining_snapshots] == ["snap-23"]
    # The kept snapshot moved to the day's last index and now holds
    # the day's final state ...
    assert (await repo.get_content_at_version(note_id, 33)).content == "v33\n"
    # ... and the next day's delta still chains on top of it.
    assert (await repo.get_content_at_version(note_id, 34)).content == "v34\n"


async def test_versioning_compaction_creates_snapshot_for_delta_only_day() -> None:
    """A day with only deltas becomes a snapshot at its last index."""
    note_id = "note-1"
    day_one = datetime(2026, 5, 17, 9, 0, 0)
    day_two = datetime(2026, 5, 18, 9, 0, 0)
    builder = NoteVersionPostgresRepo(
        snapshot_table=_DummyTable("note.version_snapshot"),
        delta_table=_DummyTable("note.version_delta"),
        max_deltas_per_snapshot=100,
    )
    snapshots = [
        {
            "snapshot_id": "snap-1",
            "note_id": note_id,
            "version_index": 1,
            "created_at": day_one,
            "author_id": "user-1",
            "title": "t",
            "content": "one\n",
        }
    ]
    deltas = [
        {
            "delta_id": "d2",
            "note_id": note_id,
            "snapshot_id": "snap-1",
            "version_index": 2,
            "created_at": day_two,
            "author_id": "user-1",
            "title_patch": "",
            "content_patch": builder._build_patch("one\n", "two\n"),
        },
        {
            "delta_id": "d3",
            "note_id": note_id,
            "snapshot_id": "snap-1",
            "version_index": 3,
            "created_at": day_two,
            "author_id": "user-1",
            "title_patch": "",
            "content_patch": builder._build_patch("two\n", "three\n"),
        },
    ]
    repo = NoteVersionPostgresRepo(
        snapshot_table=_FakeTable(
            "note.version_snapshot", snapshots, id_field="snapshot_id"
        ),
        delta_table=_FakeTable("note.version_delta", deltas),
        max_deltas_per_snapshot=100,
    )

    result = await repo.compact_deltas_before(datetime(2026, 5, 19))

    assert result.notes_compacted == 1
    assert result.deltas_removed == 2
    assert result.snapshots_removed == 0
    assert await repo._delta_table.fetch("SELECT * FROM note.version_delta") == []
    remaining_snapshots = await repo._snapshot_table.fetch(
        "SELECT * FROM note.version_snapshot"
    )
    assert len(remaining_snapshots) == 2
    assert (await repo.get_content_at_version(note_id, 3)).content == "three\n"
    assert (await repo.get_content_at_version(note_id, 1)).content == "one\n"


async def test_versioning_compacted_version_index_is_not_reconstructed() -> None:
    repo = _compaction_repo()
    await repo.compact_deltas_before(datetime(2026, 5, 19))

    try:
        await repo.get_content_at_version("note-1", 2)
    except RuntimeError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("expected removed version index to raise")


class _ContextTable:
    """Fake snapshot table that answers ``_load_append_context``.

    ``delta_count`` mirrors the SQL: it counts the deltas that
    reference the latest snapshot when the query filters on it, and
    every delta of the note otherwise.
    """

    def __init__(
        self,
        name: str,
        snapshots: list[dict],
        deltas: list[dict],
    ) -> None:
        self.name = name
        self._snapshots = snapshots
        self._deltas = deltas

    async def fetch(self, sql: str, *args):
        note_id = args[0]
        snapshots = [s for s in self._snapshots if s["note_id"] == note_id]
        latest = max(
            snapshots,
            key=lambda s: s["version_index"],
            default=None,
        )

        deltas_block = sql.split("deltas AS", 1)[1].split("versions AS", 1)[0]
        filters_by_snapshot = "FROM snap" in deltas_block
        note_deltas = [d for d in self._deltas if d["note_id"] == note_id]
        if filters_by_snapshot:
            note_deltas = [
                d
                for d in note_deltas
                if latest is not None
                and d["snapshot_id"] == latest["snapshot_id"]
            ]

        versions = [s["version_index"] for s in snapshots] + [
            d["version_index"]
            for d in self._deltas
            if d["note_id"] == note_id
        ]
        return [
            {
                "snapshot_json": (
                    json.dumps(latest, default=str)
                    if latest is not None
                    else None
                ),
                "delta_count": len(note_deltas),
                "next_version_index": max(versions, default=0) + 1,
            }
        ]


async def test_append_context_counts_only_deltas_since_latest_snapshot() -> None:
    """Regression: hitting ``max_deltas`` must not turn every later
    save into a snapshot.  ``_load_append_context`` counts only the
    deltas referencing the latest snapshot."""
    note_id = "note-1"
    snapshots = [
        {
            "snapshot_id": "snap-1",
            "note_id": note_id,
            "version_index": 1,
            "created_at": datetime(2026, 5, 18, 9, 0, 0),
            "author_id": "user-1",
            "title": "t",
            "content": "c",
        },
        {
            "snapshot_id": "snap-12",
            "note_id": note_id,
            "version_index": 12,
            "created_at": datetime(2026, 5, 18, 10, 0, 0),
            "author_id": "user-1",
            "title": "t",
            "content": "c",
        },
    ]
    deltas = [
        {
            "delta_id": f"d{index}",
            "note_id": note_id,
            "snapshot_id": "snap-1",
            "version_index": index,
            "created_at": datetime(2026, 5, 18, 9, index, 0),
        }
        for index in range(2, 12)
    ] + [
        {
            "delta_id": "d13",
            "note_id": note_id,
            "snapshot_id": "snap-12",
            "version_index": 13,
            "created_at": datetime(2026, 5, 18, 10, 1, 0),
        }
    ]
    repo = NoteVersionPostgresRepo(
        snapshot_table=_ContextTable("note.version_snapshot", snapshots, deltas),
        delta_table=_FakeTable("note.version_delta", []),
        max_deltas_per_snapshot=10,
    )

    context = await repo._load_append_context(note_id)

    assert context["delta_count"] == 1
