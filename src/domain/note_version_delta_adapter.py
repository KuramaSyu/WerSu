"""Turn a compacted day's retained snapshot into a delta row.

Compaction used to rewrite a completed day into a *full* snapshot, so a
note with years of history kept one full copy of its content per active
day.  The adapter below keeps the same day-final bytes but stores them
as a unified diff against the last version still on disk, which is what
a normal save would have written anyway.

The base version must be the version immediately *before* the day being
compacted and it must survive the compaction run.  The repository
therefore applies plans oldest-day-first and never deletes a version it
has already kept: that way the base is either the note's oldest
snapshot (the chain root) or a delta created by an earlier iteration.

A day that has no earlier version at all (the note was created and
edited on the same day) cannot be expressed as a delta, so
:meth:`DeltaVersionAdapter.adapt` returns ``None`` and the caller keeps
the retained snapshot.

The same happens when the chain already holds as many deltas as
``max_deltas`` allows: another patch would push the read cost past the
configured budget, so the day is written as a fresh snapshot and the
chain starts over from there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.domain.note_version_compaction import RetainedSnapshot
from src.domain.note_version_patch import build_patch

# Default cap on deltas between two snapshots.  ``main`` lets
# ``NOTE_VERSION_MAX_DELTAS`` override it for both appends and compaction.
DEFAULT_MAX_DELTAS_PER_SNAPSHOT = 20


@dataclass(frozen=True)
class BaseVersion:
    """The last version that stays on disk before a compacted day.

    ``snapshot_id`` is the snapshot a new delta must reference: the
    snapshot itself when the base is a snapshot, or the delta's chain
    root when the base is already a delta.
    """

    version_index: int
    snapshot_id: str
    title: str
    content: str


@dataclass(frozen=True)
class AdaptedDeltaVersion:
    """A delta row equivalent to a day's retained snapshot."""

    version_index: int
    created_at: datetime
    author_id: str
    snapshot_id: str
    title_patch: str
    content_patch: str


class DeltaVersionAdapter:
    """Express a retained snapshot as a delta against ``BaseVersion``.

    The generated patches are ``build_patch(base, retained)``, so
    applying the delta to the base reproduces the day's final bytes
    exactly (including title changes).
    """

    def adapt(
        self,
        retained: RetainedSnapshot,
        base: Optional[BaseVersion],
        *,
        deltas_since_snapshot: int = 0,
        max_deltas: int = DEFAULT_MAX_DELTAS_PER_SNAPSHOT,
    ) -> Optional[AdaptedDeltaVersion]:
        """Return the delta for ``retained``, or ``None`` for a snapshot.

        ``None`` means the day must stay a full snapshot: either there
        is no earlier version to diff against, or the base chain already
        has ``max_deltas`` patches, in which case one more would bust the
        read budget.
        """
        if base is None:
            return None
        if max_deltas >= 0 and deltas_since_snapshot >= max_deltas:
            return None

        return AdaptedDeltaVersion(
            version_index=retained.version_index,
            created_at=retained.created_at,
            author_id=retained.author_id,
            snapshot_id=base.snapshot_id,
            title_patch=build_patch(base.title, retained.title),
            content_patch=build_patch(base.content, retained.content),
        )


__all__ = [
    "AdaptedDeltaVersion",
    "BaseVersion",
    "DEFAULT_MAX_DELTAS_PER_SNAPSHOT",
    "DeltaVersionAdapter",
]