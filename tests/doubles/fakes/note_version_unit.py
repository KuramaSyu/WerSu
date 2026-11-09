from __future__ import annotations


class _FakeTable:
    """In-memory table stub to unit-test version selection logic."""

    def __init__(self, name: str, rows: list[dict]) -> None:
        self.name = name
        self._rows = rows

    async def fetch(self, sql: str, *args):
        statement = sql.lstrip().upper()

        if statement.startswith("DELETE"):
            note_id, snapshot_id, start_version, end_version = args
            before = len(self._rows)
            self._rows[:] = [
                row
                for row in self._rows
                if not (
                    row["note_id"] == note_id
                    and row["snapshot_id"] == snapshot_id
                    and start_version <= row["version_index"] < end_version
                )
            ]
            return [{"deleted": before - len(self._rows)}]

        if statement.startswith("UPDATE"):
            note_id, version_index, title_patch, content_patch = args
            matches = [
                row
                for row in self._rows
                if row["note_id"] == note_id
                and row["version_index"] == version_index
            ]
            for row in matches:
                row["title_patch"] = title_patch
                row["content_patch"] = content_patch
            return matches

        if statement.startswith("SELECT") and "WHERE" not in statement:
            return list(self._rows)

        if "WHERE note_id = $1 AND version_index = $2" in sql:
            note_id, version_index = args
            matches = [
                row
                for row in self._rows
                if row["note_id"] == note_id
                and row["version_index"] == version_index
            ]
            return matches[:1]

        if "FROM note.version_delta" in sql and "created_at <" in sql:
            (cutoff,) = args
            matches = [row for row in self._rows if row["created_at"] < cutoff]
            matches.sort(
                key=lambda row: (
                    row["note_id"],
                    row["snapshot_id"],
                    row["version_index"],
                )
            )
            return matches

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


__all__ = [
    '_FakeTable',
]
