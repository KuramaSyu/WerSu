"""Unit tests for the retained-snapshot -> delta adapter."""

from __future__ import annotations

from datetime import datetime

from src.domain.note_version_compaction import RetainedSnapshot
from src.domain.note_version_delta_adapter import (
    BaseVersion,
    DeltaVersionAdapter,
)
from src.domain.note_version_patch import apply_patch

NOTE = "note-1"
DAY = datetime(2026, 5, 19, 21, 0, 0)


def _retained(
    index: int,
    content: str,
    *,
    title: str = "t",
) -> RetainedSnapshot:
    return RetainedSnapshot(
        snapshot_id=None,
        note_id=NOTE,
        version_index=index,
        created_at=DAY,
        author_id="user-1",
        title=title,
        content=content,
    )


def test_adapt_builds_patches_from_the_base_version() -> None:
    base = BaseVersion(
        version_index=3,
        snapshot_id="snap-1",
        title="old title",
        content="one\ntwo\n",
    )
    retained = _retained(7, "one\ntwo\nthree\n", title="new title")

    adapted = DeltaVersionAdapter().adapt(retained, base)

    assert adapted is not None
    assert adapted.version_index == 7
    assert adapted.created_at == DAY
    assert adapted.author_id == "user-1"
    assert adapted.snapshot_id == "snap-1"
    assert apply_patch(adapted.title_patch, base.title) == "new title"
    assert apply_patch(adapted.content_patch, base.content) == retained.content


def test_adapt_returns_none_without_a_base() -> None:
    """A note's oldest day has nothing to diff against -> keep a snapshot."""
    assert DeltaVersionAdapter().adapt(_retained(1, "a\n"), None) is None


def test_adapt_chains_from_a_delta_root() -> None:
    """When the base is itself a delta, its chain root is reused."""
    base = BaseVersion(
        version_index=5,
        snapshot_id="snap-root",
        title="t",
        content="a\nb\n",
    )
    adapted = DeltaVersionAdapter().adapt(_retained(9, "a\nb\nc\n"), base)

    assert adapted is not None
    assert adapted.snapshot_id == "snap-root"
    assert apply_patch(adapted.content_patch, base.content) == "a\nb\nc\n"


def test_adapt_handles_unchanged_content() -> None:
    """Title-only days still produce a valid (empty) content patch."""
    base = BaseVersion(
        version_index=2, snapshot_id="snap-1", title="old", content="same\n"
    )
    adapted = DeltaVersionAdapter().adapt(
        _retained(4, "same\n", title="new"), base
    )

    assert adapted is not None
    assert adapted.content_patch == ""
    assert apply_patch(adapted.content_patch, base.content) == "same\n"
    assert apply_patch(adapted.title_patch, base.title) == "new"

def test_default_delta_budget_is_twenty() -> None:
    from src.domain.note_version_delta_adapter import (
        DEFAULT_MAX_DELTAS_PER_SNAPSHOT,
    )

    assert DEFAULT_MAX_DELTAS_PER_SNAPSHOT == 20


def test_adapt_returns_none_when_the_delta_budget_is_used_up() -> None:
    base = BaseVersion(
        version_index=1, snapshot_id="snap-1", title="t", content="a\n"
    )

    adapted = DeltaVersionAdapter().adapt(
        _retained(2, "b\n"),
        base,
        deltas_since_snapshot=20,
    )

    assert adapted is None


def test_adapt_allows_the_last_delta_of_the_budget() -> None:
    base = BaseVersion(
        version_index=1, snapshot_id="snap-1", title="t", content="a\n"
    )

    adapted = DeltaVersionAdapter().adapt(
        _retained(2, "b\n"),
        base,
        deltas_since_snapshot=19,
    )

    assert adapted is not None


def test_adapt_honours_an_explicit_max() -> None:
    base = BaseVersion(
        version_index=1, snapshot_id="snap-1", title="t", content="a\n"
    )
    adapter = DeltaVersionAdapter()

    assert (
        adapter.adapt(
            _retained(2, "b\n"), base, deltas_since_snapshot=2, max_deltas=2
        )
        is None
    )
    assert (
        adapter.adapt(
            _retained(2, "b\n"), base, deltas_since_snapshot=1, max_deltas=2
        )
        is not None
    )
