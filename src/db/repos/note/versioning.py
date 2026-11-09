from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
import difflib
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
        """Collapse deltas older than ``cutoff`` into one delta per day.

        For every note and snapshot, all deltas created on the same
        calendar day are rewritten as a single delta on the *last*
        version index of that day.  The day's earlier version indices
        are removed, so only the day's final state stays restorable.

        Groups are keyed by ``(note_id, snapshot_id, date)`` so a
        snapshot created mid-day starts a new group and older, already
        compacted days are left untouched (a one-delta group is a
        no-op).  The operation is idempotent.

        Returns:
            Counts of notes changed and delta rows removed.
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
    ) -> None:
        if max_deltas_per_snapshot < 0:
            raise ValueError("max_deltas_per_snapshot must be >= 0")

        self._snapshot_table = snapshot_table
        self._delta_table = delta_table
        self._max_deltas_per_snapshot = max_deltas_per_snapshot
        # Internal knob: a shared PostgresRowConverter instance is injected from main.py.
        self._to_postgres_row = to_postgres_row or PostgresRowConverter()
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
                SELECT COUNT(*) AS delta_count, MAX(snapshot_id::text) AS sample_id
                FROM {self._delta_table.name}
                WHERE note_id = $1
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
        sql = f"""
            SELECT delta_id, note_id, snapshot_id, version_index, created_at,
                   author_id, title_patch, content_patch
            FROM {self._delta_table.name}
            WHERE created_at < $1
            ORDER BY note_id, snapshot_id, version_index ASC
        """
        rows = await self._delta_table.fetch(sql, cutoff)
        if not rows:
            return NoteVersionCompactionResult()

        groups: dict[tuple[str, str, date], list[Record]] = {}
        for row in rows:
            group_key = (
                str(row["note_id"]),
                str(row["snapshot_id"]),
                row["created_at"].date(),
            )
            groups.setdefault(group_key, []).append(row)

        result = NoteVersionCompactionResult()
        for (note_id, snapshot_id, _day), group in groups.items():
            if len(group) < 2:
                # Already compacted (or only one save that day).
                continue

            group.sort(key=lambda row: int(row["version_index"]))
            first_index = int(group[0]["version_index"])
            last_index = int(group[-1]["version_index"])

            # Reconstruct the day's base and final state from the raw
            # version chain; the patches themselves are replaced below.
            base = await self.get_content_at_version(note_id, first_index - 1)
            tip = await self.get_content_at_version(note_id, last_index)

            title_patch = self._build_patch(base.title or "", tip.title or "")
            content_patch = self._build_patch(base.content or "", tip.content or "")

            await self._delta_table.fetch(
                f"""
                    UPDATE {self._delta_table.name}
                    SET title_patch = $3, content_patch = $4
                    WHERE note_id = $1 AND version_index = $2
                """,
                note_id,
                last_index,
                title_patch,
                content_patch,
            )
            await self._delta_table.fetch(
                f"""
                    DELETE FROM {self._delta_table.name}
                    WHERE note_id = $1 AND snapshot_id = $2
                      AND version_index >= $3 AND version_index < $4
                """,
                note_id,
                snapshot_id,
                first_index,
                last_index,
            )
            result.notes_compacted += 1
            result.deltas_removed += len(group) - 1

        return result

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
        # Use unified_diff: O(N) plus hunk headers, far smaller than ndiff for
        # large mostly-identical content.  ``n=2`` keeps a small context
        # window so the patch remains self-describing.
        old_payload = old_text if old_text.endswith("\n") else f"{old_text}\n"
        new_payload = new_text if new_text.endswith("\n") else f"{new_text}\n"
        diff_lines = self._dmp.unified_diff(
            old_payload.splitlines(keepends=True),
            new_payload.splitlines(keepends=True),
            n=2,
        )
        return "".join(diff_lines)

    def _apply_patch(self, patch_text: str, base_text: str) -> str:
        """Apply a unified diff produced by :meth:`_build_patch`.

        The patch only contains the changed hunks plus a few context
        lines, so the untouched regions of ``base_text`` must be copied
        around each hunk using the ``@@ -old_start,old_count`` offsets.
        Concatenating the payload lines alone loses everything outside
        the hunks (leading sections, table headers, trailing text, ...).
        """
        if not patch_text:
            return base_text

        # _build_patch normalises both sides to a trailing newline before
        # diffing, so hunk offsets assume a newline-terminated payload.
        # Mirror that for the base or the offsets drift on content saved
        # without a final newline.
        needs_trim = not base_text.endswith("\n")
        base_lines = base_text.splitlines(keepends=True)
        if needs_trim and base_lines:
            base_lines[-1] += "\n"

        restored: list[str] = []
        cursor = 0  # next base line that has not been consumed yet

        for line in patch_text.splitlines(keepends=True):
            # File headers are written as "--- " / "+++ " (empty file
            # names). Content lines always carry a prefix, so a line
            # starting with exactly these markers cannot be payload.
            if line.startswith("--- ") or line.startswith("+++ "):
                continue
            if line.startswith("@@"):
                # @@ -old_start[,old_count] +new_start[,new_count] @@
                header = line[2:].split("@@", 1)[0].strip()
                old_spec = header.split(" ", 1)[0].lstrip("-")
                start_text, _, count_text = old_spec.partition(",")
                old_start = int(start_text)
                old_count = int(count_text) if count_text else 1
                # Copy everything before this hunk from the base. An
                # insertion-only hunk (old_count == 0) points *after*
                # the line it inserts at.
                target = old_start - 1 if old_count > 0 else old_start
                if target > cursor:
                    restored.extend(base_lines[cursor:target])
                    cursor = target
                continue

            prefix = line[:1]
            if prefix == " ":
                # Context line: identical in base and result. Append the
                # payload (not the base line) and consume one old line.
                restored.append(line[1:])
                cursor += 1
            elif prefix == "-":
                cursor += 1
            elif prefix == "+":
                restored.append(line[1:])
            # "\\ No newline at end of file" and other markers carry no
            # payload and are ignored.

        restored.extend(base_lines[cursor:])
        result = "".join(restored)
        if needs_trim and result.endswith("\n"):
            result = result[:-1]
        return result

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
