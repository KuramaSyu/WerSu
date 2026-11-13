"""Pure planning for daily note-version compaction.

The repository owns SQL; this module owns the rules.  Feed it the
snapshots and deltas of a note history plus a cutoff and it returns a
:class:`CompactionPlan` describing which rows to keep, rewrite, insert
and delete.  That keeps the grouping/reconstruction logic testable
without a database or SQL fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

from src.domain.note_version_patch import apply_patch


@dataclass(frozen=True)
class SnapshotVersion:
    """A full-content version row."""

    snapshot_id: str
    note_id: str
    version_index: int
    created_at: datetime
    author_id: str
    title: Optional[str]
    content: str


@dataclass(frozen=True)
class DeltaVersion:
    """A patch version row that applies on top of a snapshot."""

    delta_id: str
    note_id: str
    snapshot_id: str
    version_index: int
    created_at: datetime
    author_id: str
    title_patch: Optional[str]
    content_patch: str


@dataclass(frozen=True)
class RetainedSnapshot:
    """The single version a compacted day collapses into.

    ``snapshot_id`` is ``None`` when the day had no snapshot and the
    repository must insert a new row.  Otherwise the existing row is
    rewritten in place (keeping its id so later deltas still reference
    it).

    The repository may store this as a delta instead of a full snapshot
    (see :class:`~src.domain.note_version_delta_adapter.DeltaVersionAdapter`).
    In that case neither the snapshot nor its id is kept; the fields
    only describe the day's final bytes.
    """

    snapshot_id: Optional[str]
    note_id: str
    version_index: int
    created_at: datetime
    author_id: str
    title: str
    content: str

    @property
    def is_new(self) -> bool:
        return self.snapshot_id is None


@dataclass(frozen=True)
class CompactedDay:
    """One completed day's collapse instructions."""

    note_id: str
    day: date
    first_version_index: int
    retained_snapshot: RetainedSnapshot
    delta_ids: tuple[str, ...]
    snapshot_ids_to_delete: tuple[str, ...]


@dataclass(frozen=True)
class CompactionPlan:
    """Everything the repository needs to apply a compaction run."""

    days: tuple[CompactedDay, ...] = ()

    @property
    def days_oldest_first(self) -> tuple[CompactedDay, ...]:
        """Days ordered so a note's history is rewritten from the start.

        Applying an older day first means the base of the next day's
        delta is already the version that survives, not one this run
        will delete.
        """
        return tuple(
            sorted(
                self.days,
                key=lambda day: (day.note_id, day.first_version_index),
            )
        )

    @property
    def notes_compacted(self) -> int:
        return len({day.note_id for day in self.days})

    @property
    def deltas_removed(self) -> int:
        return sum(len(day.delta_ids) for day in self.days)

    @property
    def snapshots_removed(self) -> int:
        return sum(len(day.snapshot_ids_to_delete) for day in self.days)


@dataclass(frozen=True)
class _Content:
    title: str
    content: str
    created_at: datetime
    author_id: str


class DailyVersionCompactor:
    """Collapse each completed day's versions into one day-final entry.

    A day with fewer than two versions is left alone, which makes the
    operation idempotent.  The retained entry carries the day's final
    state at the day's last version index, so no bytes are lost; how it
    is stored (full snapshot or delta against the previous version) is
    the repository's decision.
    """

    def plan(
        self,
        snapshots: Iterable[SnapshotVersion],
        deltas: Iterable[DeltaVersion],
        cutoff: datetime,
    ) -> CompactionPlan:
        """Return the collapse plan for every version before ``cutoff``."""
        snapshots = [s for s in snapshots if s.created_at < cutoff]
        deltas = [d for d in deltas if d.created_at < cutoff]

        snapshots_by_note: dict[str, list[SnapshotVersion]] = {}
        for snapshot in snapshots:
            snapshots_by_note.setdefault(snapshot.note_id, []).append(snapshot)
        deltas_by_note: dict[str, list[DeltaVersion]] = {}
        for delta in deltas:
            deltas_by_note.setdefault(delta.note_id, []).append(delta)

        groups: dict[
            tuple[str, date], list[SnapshotVersion | DeltaVersion]
        ] = {}
        for snapshot in snapshots:
            key = (snapshot.note_id, snapshot.created_at.date())
            groups.setdefault(key, []).append(snapshot)
        for delta in deltas:
            key = (delta.note_id, delta.created_at.date())
            groups.setdefault(key, []).append(delta)

        compacted: list[CompactedDay] = []
        for (note_id, day), versions in groups.items():
            if len(versions) < 2:
                # Already compacted (or only one save that day).
                continue

            versions.sort(key=lambda version: version.version_index)
            first_index = versions[0].version_index
            last_index = versions[-1].version_index
            final = self._content_at(
                note_id,
                last_index,
                snapshots_by_note.get(note_id, []),
                deltas_by_note.get(note_id, []),
            )

            day_snapshots = [
                version
                for version in versions
                if isinstance(version, SnapshotVersion)
            ]
            keep = day_snapshots[-1] if day_snapshots else None
            retained = RetainedSnapshot(
                snapshot_id=keep.snapshot_id if keep is not None else None,
                note_id=note_id,
                version_index=last_index,
                created_at=final.created_at,
                author_id=final.author_id,
                title=final.title,
                content=final.content,
            )
            compacted.append(
                CompactedDay(
                    note_id=note_id,
                    day=day,
                    first_version_index=first_index,
                    retained_snapshot=retained,
                    delta_ids=tuple(
                        version.delta_id
                        for version in versions
                        if isinstance(version, DeltaVersion)
                    ),
                    snapshot_ids_to_delete=tuple(
                        snapshot.snapshot_id for snapshot in day_snapshots[:-1]
                    ),
                )
            )

        return CompactionPlan(days=tuple(compacted))

    @staticmethod
    def _content_at(
        note_id: str,
        target: int,
        snapshots: list[SnapshotVersion],
        deltas: list[DeltaVersion],
    ) -> _Content:
        """Reconstruct the state at ``target`` from the raw version chain."""
        base: Optional[SnapshotVersion] = None
        for snapshot in sorted(snapshots, key=lambda s: s.version_index):
            if snapshot.version_index <= target:
                base = snapshot
            else:
                break
        if base is None:
            raise ValueError(
                f"No snapshot at or before version {target} for note {note_id}"
            )

        title = base.title or ""
        content = base.content or ""
        created_at = base.created_at
        author_id = base.author_id
        if base.version_index == target:
            return _Content(title, content, created_at, author_id)

        for delta in sorted(deltas, key=lambda d: d.version_index):
            if base.version_index < delta.version_index <= target:
                title = apply_patch(delta.title_patch or "", title)
                content = apply_patch(delta.content_patch or "", content)
                created_at = delta.created_at
                author_id = delta.author_id
        return _Content(title, content, created_at, author_id)


__all__ = [
    "CompactedDay",
    "CompactionPlan",
    "DailyVersionCompactor",
    "DeltaVersion",
    "RetainedSnapshot",
    "SnapshotVersion",
]
