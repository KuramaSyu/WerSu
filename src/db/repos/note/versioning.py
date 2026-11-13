from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, time, timedelta
import json
from typing import List, Optional

from asyncpg import Record

from src.db.entities.note.versioning import (
    NoteVersionCompactionResult,
    NoteVersionContent,
    NoteVersionDeltaEntity,
    NoteVersionEntry,
    NoteVersionSnapshotEntity,
)
from src.db.table import TableABC
from src.domain.note_version_compaction import (
    DailyVersionCompactor,
    DeltaVersion,
    SnapshotVersion,
)
from src.domain.note_version_delta_adapter import (
    BaseVersion,
    DeltaVersionAdapter,
)
from src.domain.note_version_patch import apply_patch, build_patch
from src.grpc_mod.converter.postgres_row_converter import (
    PostgresRowConverter,
)
from src.utils import asdict


class NoteVersionRepoABC(ABC):
    """Abstract repository for note content versioning."""

    @property
    @abstractmethod
    def max_deltas_per_snapshot(self) -> int:
        """Maximum deltas before forcing a new snapshot."""
        ...

    @abstractmethod
    async def record_initial_snapshot(
        self,
        note_id: str,
        title: Optional[str],
        content: Optional[str],
        author_id: str,
        created_at: datetime,
    ) -> NoteVersionSnapshotEntity:
        """Create the first snapshot for a note."""
        ...

    @abstractmethod
    async def append_version(
        self,
        note_id: str,
        old_title: Optional[str],
        old_content: Optional[str],
        new_title: Optional[str],
        new_content: Optional[str],
        author_id: str,
        created_at: datetime,
    ) -> Optional[NoteVersionEntry]:
        """Append a new version entry (snapshot or delta)."""
        ...

    @abstractmethod
    async def list_versions(
        self,
        note_id: str,
        limit: int,
        offset: int,
    ) -> List[NoteVersionEntry]:
        """List versions in descending order by version index."""
        ...

    @abstractmethod
    async def get_content_at_version(
        self,
        note_id: str,
        version_index: int,
    ) -> NoteVersionContent:
        """Reconstruct note content at a specific version index."""
        ...

    @abstractmethod
    async def compact_deltas_before(
        self,
        cutoff: datetime,
    ) -> NoteVersionCompactionResult:
        """Collapse each completed day's versions into one entry.

        For every note, all snapshots and deltas created on the same
        calendar day (before ``cutoff``) are replaced by a single
        version that holds the day's final state at the day's last
        version index.  When a version from an earlier day survives,
        that final state is stored as a *delta* against it
        (``DeltaVersionAdapter``), so a long history stays one initial
        snapshot plus at most ``max_deltas_per_snapshot`` patches between
        snapshots.  When that budget is used up, the day is written as a
        fresh snapshot instead, starting a new chain.  A note's oldest
        day - which has nothing to diff against - always keeps a full
        snapshot, acting as the chain root.

        Days are applied oldest first per note, so each delta's base is
        a version that this run keeps rather than one a later iteration
        deletes.  Deltas of later days that referenced a removed
        snapshot are re-pointed at the surviving chain root before the
        delete, keeping the FK cascade from taking them down.

        Groups are keyed by ``(note_id, date)``.  A day with a single
        version is a no-op, and the operation is idempotent.

        Returns:
            Counts of notes changed and delta/snapshot rows removed.
        """
        ...


class NoteVersionPostgresRepo(NoteVersionRepoABC):
    """Postgres implementation of the note versioning repository."""

    def __init__(
        self,
        snapshot_table: TableABC,
        delta_table: TableABC,
        max_deltas_per_snapshot: int,
        to_postgres_row: Optional[PostgresRowConverter] = None,
        compactor: Optional[DailyVersionCompactor] = None,
        delta_adapter: Optional[DeltaVersionAdapter] = None,
    ) -> None:
        if max_deltas_per_snapshot < 0:
            raise ValueError("max_deltas_per_snapshot must be >= 0")

        self._snapshot_table = snapshot_table
        self._delta_table = delta_table
        self._max_deltas_per_snapshot = max_deltas_per_snapshot
        # Internal knob: a shared PostgresRowConverter instance is injected from main.py.
        self._to_postgres_row = to_postgres_row or PostgresRowConverter()
        self._compactor = compactor or DailyVersionCompactor()
        self._delta_adapter = delta_adapter or DeltaVersionAdapter()

    @property
    def max_deltas_per_snapshot(self) -> int:
        return self._max_deltas_per_snapshot

    async def record_initial_snapshot(
        self,
        note_id: str,
        title: Optional[str],
        content: Optional[str],
        author_id: str,
        created_at: datetime,
    ) -> NoteVersionSnapshotEntity:
        """Create a snapshot entry for the given note content."""
        version_index = await self._get_next_version_index(note_id)
        record = await self._insert_snapshot(
            NoteVersionSnapshotEntity(
                note_id=note_id,
                version_index=version_index,
                created_at=created_at,
                author_id=author_id,
                title=title,
                content=content,
            )
        )
        return record

    async def append_version(
        self,
        note_id: str,
        old_title: Optional[str],
        old_content: Optional[str],
        new_title: Optional[str],
        new_content: Optional[str],
        author_id: str,
        created_at: datetime,
    ) -> Optional[NoteVersionEntry]:
        """Append a new version, choosing snapshot or delta based on thresholds."""
        old_title_norm = old_title or ""
        new_title_norm = new_title or ""
        old_content_norm = old_content or ""
        new_content_norm = new_content or ""

        if old_title_norm == new_title_norm and old_content_norm == new_content_norm:
            # Avoid creating empty version entries if nothing changed.
            return None

        context = await self._load_append_context(note_id)
        latest_snapshot = context["snapshot"]
        delta_count = context["delta_count"]
        version_index = context["next_version_index"]

        if latest_snapshot is None:
            # If we are missing history, the safe fallback is a full snapshot.
            snapshot = await self.record_initial_snapshot(
                note_id=note_id,
                title=new_title,
                content=new_content,
                author_id=author_id,
                created_at=created_at,
            )
            return self._to_entry_from_snapshot(snapshot)

        if delta_count >= self._max_deltas_per_snapshot:
            # Threshold reached → store a full snapshot for faster restore.
            snapshot = await self._insert_snapshot(
                NoteVersionSnapshotEntity(
                    note_id=note_id,
                    version_index=version_index,
                    created_at=created_at,
                    author_id=author_id,
                    title=new_title,
                    content=new_content,
                )
            )
            return self._to_entry_from_snapshot(snapshot)

        title_patch = self._build_patch(old_title_norm, new_title_norm)
        content_patch = self._build_patch(old_content_norm, new_content_norm)
        # Store delta patch text for title/content; reconstruction applies patches in order.
        delta = await self._insert_delta(
            NoteVersionDeltaEntity(
                note_id=note_id,
                snapshot_id=latest_snapshot.snapshot_id,
                version_index=version_index,
                created_at=created_at,
                author_id=author_id,
                title_patch=title_patch,
                content_patch=content_patch,
            )
        )
        return self._to_entry_from_delta(delta)

    async def _load_append_context(self, note_id: str) -> dict:
        """Fetch everything `append_version` needs to decide snapshot vs delta.

        Single round-trip: returns the latest snapshot row, the
        number of deltas that already reference it, and the next
        free ``version_index`` for the note.
        """
        sql = f"""
            WITH snap AS (
                SELECT snapshot_id, note_id, version_index, created_at,
                       author_id, title, content
                FROM {self._snapshot_table.name}
                WHERE note_id = $1
                ORDER BY version_index DESC
                LIMIT 1
            ),
            deltas AS (
                SELECT COUNT(*) AS delta_count
                FROM {self._delta_table.name}
                WHERE note_id = $1
                  AND snapshot_id = (SELECT snapshot_id FROM snap)
            ),
            versions AS (
                SELECT version_index FROM {self._snapshot_table.name} WHERE note_id = $1
                UNION ALL
                SELECT version_index FROM {self._delta_table.name} WHERE note_id = $1
            )
            SELECT
                (SELECT row_to_json(s) FROM snap s) AS snapshot_json,
                (SELECT delta_count FROM deltas) AS delta_count,
                COALESCE((SELECT MAX(version_index) FROM versions), 0) + 1 AS next_version_index
        """
        rows = await self._snapshot_table.fetch(sql, note_id)
        if not rows:
            return {
                "snapshot": None,
                "delta_count": 0,
                "next_version_index": 1,
            }
        row = rows[0]
        snapshot_json = row["snapshot_json"]
        snapshot: Optional[NoteVersionSnapshotEntity] = None
        if snapshot_json is not None:
            # asyncpg returns ``row_to_json`` as a JSON string; decode
            # so we can feed the dict through ``_snapshot_from_record``.
            snapshot = self._snapshot_from_record(
                _JsonRecord(json.loads(snapshot_json))
            )
        return {
            "snapshot": snapshot,
            "delta_count": int(row["delta_count"] or 0),
            "next_version_index": int(row["next_version_index"]),
        }

    async def list_versions(
        self,
        note_id: str,
        limit: int,
        offset: int,
    ) -> List[NoteVersionEntry]:
        # RA: τ_{version_index↓}(σ_{note_id=?}(Snapshots ∪ Deltas))
        sql = f"""
            SELECT
                'snapshot' AS kind,
                snapshot_id AS version_id,
                note_id,
                version_index,
                created_at,
                author_id,
                snapshot_id AS snapshot_ref
            FROM {self._snapshot_table.name}
            WHERE note_id = $1
            UNION ALL
            SELECT
                'delta' AS kind,
                delta_id AS version_id,
                note_id,
                version_index,
                created_at,
                author_id,
                snapshot_id AS snapshot_ref
            FROM {self._delta_table.name}
            WHERE note_id = $1
            ORDER BY version_index DESC
            LIMIT $2 OFFSET $3
        """
        rows = await self._snapshot_table.fetch(sql, note_id, limit, offset)
        if not rows:
            return []

        entries = []
        for row in rows:
            entries.append(
                NoteVersionEntry(
                    version_id=str(row["version_id"]),
                    note_id=str(row["note_id"]),
                    version_index=int(row["version_index"]),
                    created_at=row["created_at"],
                    author_id=str(row["author_id"]),
                    is_snapshot=row["kind"] == "snapshot",
                    snapshot_id=str(row["snapshot_ref"]) if row["snapshot_ref"] else None,
                )
            )
        return entries

    async def get_content_at_version(
        self,
        note_id: str,
        version_index: int,
    ) -> NoteVersionContent:
        # Compaction removes old version indices. A stale client still
        # holding the pre-compaction list must get an error instead of
        # silently reconstructing an older base state as if it were
        # the requested version.
        if not await self._version_exists(note_id, version_index):
            raise RuntimeError(
                f"Version {version_index} does not exist for note {note_id}"
            )

        snapshot = await self._get_snapshot_for_version(note_id, version_index)
        if snapshot is None:
            raise RuntimeError(f"No snapshot found for note {note_id} at version {version_index}")

        title = snapshot.title or ""
        content = snapshot.content or ""
        created_at = snapshot.created_at
        author_id = snapshot.author_id

        if snapshot.version_index == version_index:
            return NoteVersionContent(
                note_id=note_id,
                version_index=version_index,
                created_at=created_at,
                author_id=author_id,
                title=title,
                content=content,
            )

        deltas = await self._get_deltas_between_versions(
            note_id=note_id,
            start_version=int(snapshot.version_index),
            end_version=version_index,
        )
        for delta in deltas:
            title = self._apply_patch(delta.title_patch or "", title)
            content = self._apply_patch(delta.content_patch or "", content)
            created_at = delta.created_at
            author_id = delta.author_id

        return NoteVersionContent(
            note_id=note_id,
            version_index=version_index,
            created_at=created_at,
            author_id=author_id,
            title=title,
            content=content,
        )

    async def compact_deltas_before(
        self,
        cutoff: datetime,
    ) -> NoteVersionCompactionResult:
        snapshot_sql = f"""
            SELECT snapshot_id, note_id, version_index, created_at,
                   author_id, title, content
            FROM {self._snapshot_table.name}
            WHERE created_at < $1
            ORDER BY note_id, version_index ASC
        """
        delta_sql = f"""
            SELECT delta_id, note_id, snapshot_id, version_index, created_at,
                   author_id, title_patch, content_patch
            FROM {self._delta_table.name}
            WHERE created_at < $1
            ORDER BY note_id, version_index ASC
        """
        snapshot_rows = await self._snapshot_table.fetch(snapshot_sql, cutoff) or []
        delta_rows = await self._delta_table.fetch(delta_sql, cutoff) or []
        if not snapshot_rows and not delta_rows:
            return NoteVersionCompactionResult()

        plan = self._compactor.plan(
            snapshots=[
                SnapshotVersion(
                    snapshot_id=str(row["snapshot_id"]),
                    note_id=str(row["note_id"]),
                    version_index=int(row["version_index"]),
                    created_at=row["created_at"],
                    author_id=str(row["author_id"]),
                    title=row["title"],
                    content=row["content"] or "",
                )
                for row in snapshot_rows
            ],
            deltas=[
                DeltaVersion(
                    delta_id=str(row["delta_id"]),
                    note_id=str(row["note_id"]),
                    snapshot_id=str(row["snapshot_id"]),
                    version_index=int(row["version_index"]),
                    created_at=row["created_at"],
                    author_id=str(row["author_id"]),
                    title_patch=row["title_patch"],
                    content_patch=row["content_patch"] or "",
                )
                for row in delta_rows
            ],
            cutoff=cutoff,
        )

        # Oldest day first, per note: the base of each delta is then a
        # version this run keeps, never one a later iteration removes.
        deltas_removed = 0
        snapshots_removed = 0
        # Every delta the run will collapse, so budget counts only measure
        # the chain that actually survives this run.
        compacted_delta_ids = [
            delta_id for compacted in plan.days for delta_id in compacted.delta_ids
        ]
        for day in plan.days_oldest_first:
            day_start = datetime.combine(day.day, time.min)
            day_end = day_start + timedelta(days=1)

            # Resolve the version right before this day *before* touching
            # any rows, so its reconstructed content is stable.
            previous = await self._get_latest_version_before(
                day.note_id, day.first_version_index
            )
            base: Optional[BaseVersion] = None
            if previous is not None and previous.snapshot_id is not None:
                base_content = await self.get_content_at_version(
                    day.note_id, previous.version_index
                )
                base = BaseVersion(
                    version_index=previous.version_index,
                    snapshot_id=previous.snapshot_id,
                    title=base_content.title,
                    content=base_content.content,
                )
            # A note's oldest day has nothing to diff against, so it stays
            # a snapshot and becomes the chain root.

            # Drop the day's deltas first: deleting a snapshot cascades
            # into the deltas that reference it, and we want the counts
            # to stay exact.
            await self._delta_table.fetch(
                f"""
                    DELETE FROM {self._delta_table.name}
                    WHERE note_id = $1
                      AND created_at >= $2 AND created_at < $3
                """,
                day.note_id,
                day_start,
                day_end,
            )
            deltas_removed += len(day.delta_ids)

            # Only diff against the base while its chain has budget left;
            # once it holds ``max_deltas`` patches the day is a snapshot.
            adapted = None
            if base is not None:
                deltas_since_snapshot = await self._count_deltas_since_snapshot(
                    day.note_id,
                    base.snapshot_id,
                    exclude_ids=compacted_delta_ids,
                )
                adapted = self._delta_adapter.adapt(
                    day.retained_snapshot,
                    base,
                    deltas_since_snapshot=deltas_since_snapshot,
                    max_deltas=self._max_deltas_per_snapshot,
                )

            retained = day.retained_snapshot
            snapshots_to_delete = list(day.snapshot_ids_to_delete)
            if adapted is not None:
                # The day becomes a delta, so even the retained snapshot
                # row goes away; later deltas are re-pointed at the chain
                # root so the FK cascade cannot take them down with it.
                if retained.snapshot_id is not None:
                    snapshots_to_delete.append(retained.snapshot_id)
                await self._repoint_deltas(
                    day.note_id, snapshots_to_delete, adapted.snapshot_id
                )
                for snapshot_id in snapshots_to_delete:
                    await self._delete_snapshot(snapshot_id)
                snapshots_removed += len(snapshots_to_delete)
                await self._insert_delta(
                    NoteVersionDeltaEntity(
                        note_id=day.note_id,
                        snapshot_id=adapted.snapshot_id,
                        version_index=adapted.version_index,
                        created_at=adapted.created_at,
                        author_id=adapted.author_id,
                        title_patch=adapted.title_patch,
                        content_patch=adapted.content_patch,
                    )
                )
                continue

            # No base version: keep the retained snapshot (full content).
            if retained.is_new:
                kept = await self._insert_snapshot(
                    NoteVersionSnapshotEntity(
                        note_id=retained.note_id,
                        version_index=retained.version_index,
                        created_at=retained.created_at,
                        author_id=retained.author_id,
                        title=retained.title,
                        content=retained.content,
                    )
                )
                kept_snapshot_id = str(kept.snapshot_id)
            else:
                await self._snapshot_table.fetch(
                    f"""
                        UPDATE {self._snapshot_table.name}
                        SET version_index = $2, created_at = $3,
                            author_id = $4, title = $5, content = $6
                        WHERE snapshot_id = $1
                    """,
                    retained.snapshot_id,
                    retained.version_index,
                    retained.created_at,
                    retained.author_id,
                    retained.title,
                    retained.content,
                )
                kept_snapshot_id = str(retained.snapshot_id)

            await self._repoint_deltas(
                day.note_id, snapshots_to_delete, kept_snapshot_id
            )
            for snapshot_id in snapshots_to_delete:
                await self._delete_snapshot(snapshot_id)
            snapshots_removed += len(snapshots_to_delete)

        return NoteVersionCompactionResult(
            notes_compacted=plan.notes_compacted,
            deltas_removed=deltas_removed,
            snapshots_removed=snapshots_removed,
        )

    async def _get_latest_version_before(
        self,
        note_id: str,
        version_index: int,
    ) -> Optional[NoteVersionEntry]:
        """Return the newest surviving version strictly before an index.

        Used as the diff base for a compacted day.  Queried per table so
        both a snapshot and a delta can be the base.
        """
        snapshot_sql = f"""
            SELECT 'snapshot' AS kind, snapshot_id AS version_id, note_id,
                   version_index, created_at, author_id, snapshot_id AS snapshot_ref
            FROM {self._snapshot_table.name}
            WHERE note_id = $1 AND version_index < $2
            ORDER BY version_index DESC
            LIMIT 1
        """
        delta_sql = f"""
            SELECT 'delta' AS kind, delta_id AS version_id, note_id,
                   version_index, created_at, author_id, snapshot_id AS snapshot_ref
            FROM {self._delta_table.name}
            WHERE note_id = $1 AND version_index < $2
            ORDER BY version_index DESC
            LIMIT 1
        """
        snapshot_rows = await self._snapshot_table.fetch(
            snapshot_sql, note_id, version_index
        ) or []
        delta_rows = await self._delta_table.fetch(
            delta_sql, note_id, version_index
        ) or []

        candidates = [
            self._entry_from_row(row) for row in [*snapshot_rows, *delta_rows]
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda entry: entry.version_index)

    async def _repoint_deltas(
        self,
        note_id: str,
        snapshot_ids: list[str],
        keep_snapshot_id: str,
    ) -> None:
        """Move deltas off snapshots a compaction is about to delete.

        Reads order deltas by ``version_index`` only, so rewriting the
        ``snapshot_id`` FK changes nothing about reconstruction but keeps
        the ``ON DELETE CASCADE`` from removing later days' deltas.
        """
        if not snapshot_ids:
            return
        await self._delta_table.fetch(
            f"""
                UPDATE {self._delta_table.name}
                SET snapshot_id = $3
                WHERE note_id = $1 AND snapshot_id = ANY($2::text[])
            """,
            note_id,
            snapshot_ids,
            keep_snapshot_id,
        )

    async def _delete_snapshot(self, snapshot_id: str) -> None:
        await self._snapshot_table.fetch(
            f"""
                DELETE FROM {self._snapshot_table.name}
                WHERE snapshot_id = $1
            """,
            snapshot_id,
        )

    @staticmethod
    def _entry_from_row(row: Record) -> NoteVersionEntry:
        return NoteVersionEntry(
            version_id=str(row["version_id"]),
            note_id=str(row["note_id"]),
            version_index=int(row["version_index"]),
            created_at=row["created_at"],
            author_id=str(row["author_id"]),
            is_snapshot=row["kind"] == "snapshot",
            snapshot_id=str(row["snapshot_ref"]) if row["snapshot_ref"] else None,
        )

    async def _version_exists(self, note_id: str, version_index: int) -> bool:
        for table in (self._snapshot_table, self._delta_table):
            rows = await table.fetch(
                f"""
                    SELECT version_index
                    FROM {table.name}
                    WHERE note_id = $1 AND version_index = $2
                    LIMIT 1
                """,
                note_id,
                version_index,
            )
            if rows:
                return True
        return False

    async def _get_latest_snapshot(self, note_id: str) -> Optional[NoteVersionSnapshotEntity]:
        sql = f"""
            SELECT snapshot_id, note_id, version_index, created_at, author_id, title, content
            FROM {self._snapshot_table.name}
            WHERE note_id = $1
            ORDER BY version_index DESC
            LIMIT 1
        """
        rows = await self._snapshot_table.fetch(sql, note_id)
        if not rows:
            return None
        return self._snapshot_from_record(rows[0])

    async def _get_snapshot_for_version(
        self,
        note_id: str,
        version_index: int,
    ) -> Optional[NoteVersionSnapshotEntity]:
        # RA: τ_{version_index↓}(σ_{note_id=? ∧ version_index≤?}(Snapshots))
        sql = f"""
            SELECT snapshot_id, note_id, version_index, created_at, author_id, title, content
            FROM {self._snapshot_table.name}
            WHERE note_id = $1 AND version_index <= $2
            ORDER BY version_index DESC
            LIMIT 1
        """
        rows = await self._snapshot_table.fetch(sql, note_id, version_index)
        if not rows:
            return None
        return self._snapshot_from_record(rows[0])

    async def _get_deltas_between_versions(
        self,
        note_id: str,
        start_version: int,
        end_version: int,
    ) -> List[NoteVersionDeltaEntity]:
        # RA: τ_{version_index↑}(σ_{note_id=? ∧ start<version_index≤end}(Deltas))
        sql = f"""
            SELECT delta_id, note_id, snapshot_id, version_index, created_at, author_id, title_patch, content_patch
            FROM {self._delta_table.name}
            WHERE note_id = $1 AND version_index > $2 AND version_index <= $3
            ORDER BY version_index ASC
        """
        rows = await self._delta_table.fetch(sql, note_id, start_version, end_version)
        if not rows:
            return []
        return [self._delta_from_record(row) for row in rows]

    async def _count_deltas_since_snapshot(
        self,
        note_id: str,
        snapshot_id: str,
        exclude_ids: Optional[List[str]] = None,
    ) -> int:
        # RA: γ_{count(*)}(σ_{note_id=? ∧ snapshot_id=? ∧ delta_id∉excluded}(Deltas))
        # ``exclude_ids`` lets a catch-up run ignore the deltas it is
        # about to collapse, so the surviving chain's budget is measured.
        sql = f"""
            SELECT COUNT(*) AS delta_count
            FROM {self._delta_table.name}
            WHERE note_id = $1 AND snapshot_id = $2
              AND NOT (delta_id = ANY($3::text[]))
        """
        rows = await self._delta_table.fetch(
            sql, note_id, snapshot_id, exclude_ids or []
        )
        if not rows:
            return 0
        return int(rows[0]["delta_count"])

    async def _get_next_version_index(self, note_id: str) -> int:
        # RA: 1 + γ_{max(version_index)}(σ_{note_id=?}(Snapshots ∪ Deltas))
        sql = f"""
            SELECT COALESCE(MAX(version_index), 0) AS max_version
            FROM (
                SELECT version_index FROM {self._snapshot_table.name} WHERE note_id = $1
                UNION ALL
                SELECT version_index FROM {self._delta_table.name} WHERE note_id = $1
            ) AS versions
        """
        rows = await self._snapshot_table.fetch(sql, note_id)
        if not rows:
            return 1
        return int(rows[0]["max_version"]) + 1

    async def _insert_snapshot(self, entity: NoteVersionSnapshotEntity) -> NoteVersionSnapshotEntity:
        # Delegate entity -> row conversion to the PostgresRowConverter visitor.
        record = await self._snapshot_table.insert(
            entity.convert(self._to_postgres_row),
            returning="snapshot_id, note_id, version_index, created_at, author_id, title, content",
        )
        if not record:
            raise RuntimeError("Failed to insert note version snapshot")
        return self._snapshot_from_record(record[0])

    async def _insert_delta(self, entity: NoteVersionDeltaEntity) -> NoteVersionDeltaEntity:
        # Same visitor delegation as _insert_snapshot.
        record = await self._delta_table.insert(
            entity.convert(self._to_postgres_row),
            returning="delta_id, note_id, snapshot_id, version_index, created_at, author_id, title_patch, content_patch",
        )
        if not record:
            raise RuntimeError("Failed to insert note version delta")
        return self._delta_from_record(record[0])

    def _snapshot_from_record(self, record: Record) -> NoteVersionSnapshotEntity:
        return NoteVersionSnapshotEntity(
            snapshot_id=record["snapshot_id"],
            note_id=record["note_id"],
            version_index=record["version_index"],
            created_at=record["created_at"],
            author_id=record["author_id"],
            title=record["title"],
            content=record["content"],
        )

    def _delta_from_record(self, record: Record) -> NoteVersionDeltaEntity:
        return NoteVersionDeltaEntity(
            delta_id=record["delta_id"],
            note_id=record["note_id"],
            snapshot_id=record["snapshot_id"],
            version_index=record["version_index"],
            created_at=record["created_at"],
            author_id=record["author_id"],
            title_patch=record["title_patch"],
            content_patch=record["content_patch"],
        )

    def _build_patch(self, old_text: str, new_text: str) -> str:
        """Return a unified diff from ``old_text`` to ``new_text``."""
        return build_patch(old_text, new_text)

    def _apply_patch(self, patch_text: str, base_text: str) -> str:
        """Apply a unified diff produced by :meth:`_build_patch`."""
        return apply_patch(patch_text, base_text)

    @staticmethod
    def _to_entry_from_snapshot(snapshot: NoteVersionSnapshotEntity) -> NoteVersionEntry:
        return NoteVersionEntry(
            version_id=str(snapshot.snapshot_id),
            note_id=str(snapshot.note_id),
            version_index=int(snapshot.version_index),
            created_at=snapshot.created_at,
            author_id=str(snapshot.author_id),
            is_snapshot=True,
            snapshot_id=str(snapshot.snapshot_id),
        )

    @staticmethod
    def _to_entry_from_delta(delta: NoteVersionDeltaEntity) -> NoteVersionEntry:
        return NoteVersionEntry(
            version_id=str(delta.delta_id),
            note_id=str(delta.note_id),
            version_index=int(delta.version_index),
            created_at=delta.created_at,
            author_id=str(delta.author_id),
            is_snapshot=False,
            snapshot_id=str(delta.snapshot_id),
        )


class _JsonRecord:
    """Dict adapter that exposes asyncpg ``Record``-style key access.

    ``row_to_json`` returns a plain ``dict``; this wrapper lets us
    pass that dict through :meth:`_snapshot_from_record` without
    rewriting that method to accept both shapes.
    """

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __getitem__(self, key: str) -> Any:
        return self._payload[key]
