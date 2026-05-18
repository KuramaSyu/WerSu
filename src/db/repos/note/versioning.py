from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
import difflib
from typing import List, Optional

from asyncpg import Record

from src.db.entities.note.versioning import (
    NoteVersionContent,
    NoteVersionDeltaEntity,
    NoteVersionEntry,
    NoteVersionSnapshotEntity,
)
from src.db.table import TableABC
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


class NoteVersionPostgresRepo(NoteVersionRepoABC):
    """Postgres implementation of the note versioning repository."""

    def __init__(
        self,
        snapshot_table: TableABC[List[Record]],
        delta_table: TableABC[List[Record]],
        max_deltas_per_snapshot: int,
    ) -> None:
        if max_deltas_per_snapshot < 0:
            raise ValueError("max_deltas_per_snapshot must be >= 0")

        self._snapshot_table = snapshot_table
        self._delta_table = delta_table
        self._max_deltas_per_snapshot = max_deltas_per_snapshot
        self._dmp = difflib

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

        latest_snapshot = await self._get_latest_snapshot(note_id)
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

        delta_count = await self._count_deltas_since_snapshot(
            note_id=note_id,
            snapshot_id=str(latest_snapshot.snapshot_id),
        )
        version_index = await self._get_next_version_index(note_id)

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

    async def _count_deltas_since_snapshot(self, note_id: str, snapshot_id: str) -> int:
        # RA: γ_{count(*)}(σ_{note_id=? ∧ snapshot_id=?}(Deltas))
        sql = f"""
            SELECT COUNT(*) AS delta_count
            FROM {self._delta_table.name}
            WHERE note_id = $1 AND snapshot_id = $2
        """
        rows = await self._delta_table.fetch(sql, note_id, snapshot_id)
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
        record = await self._snapshot_table.insert(
            asdict(entity),
            returning="snapshot_id, note_id, version_index, created_at, author_id, title, content",
        )
        if not record:
            raise RuntimeError("Failed to insert note version snapshot")
        return self._snapshot_from_record(record[0])

    async def _insert_delta(self, entity: NoteVersionDeltaEntity) -> NoteVersionDeltaEntity:
        record = await self._delta_table.insert(
            asdict(entity),
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
        # Ensure diffs always include a line terminator so difflib restores correctly
        old_payload = old_text if old_text.endswith("\n") else f"{old_text}\n"
        new_payload = new_text if new_text.endswith("\n") else f"{new_text}\n"
        diff_lines = self._dmp.ndiff(
            old_payload.splitlines(keepends=True),
            new_payload.splitlines(keepends=True),
        )
        return "".join(diff_lines)

    def _apply_patch(self, patch_text: str, base_text: str) -> str:
        if not patch_text:
            return base_text
        needs_trim = not base_text.endswith("\n")
        base_payload = base_text if base_text.endswith("\n") else f"{base_text}\n"
        diff_lines = patch_text.splitlines(keepends=True)
        restored = "".join(self._dmp.restore(diff_lines, 2))
        if needs_trim and restored.endswith("\n"):
            restored = restored[:-1]
        return restored

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
