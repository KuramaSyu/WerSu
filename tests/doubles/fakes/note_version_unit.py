from __future__ import annotations


class _FakeTable:
    """In-memory table stub to unit-test version selection logic."""

    def __init__(self, name: str, rows: list[dict], id_field: str | None = None) -> None:
        self.name = name
        self._rows = rows
        self._id_field = id_field
        self._generated = 0

    async def insert(self, where: dict, returning: str = "*", on_conflict: str = ""):
        row = dict(where)
        if self._id_field and not row.get(self._id_field):
            self._generated += 1
            row[self._id_field] = f"generated-{self._id_field}-{self._generated}"
        self._rows.append(row)
        return [row]

    async def fetch(self, sql: str, *args):
        statement = sql.lstrip().upper()

        if statement.startswith("DELETE"):
            return self._delete(sql, args)

        if statement.startswith("UPDATE"):
            return self._update(sql, args)

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

        if "FROM note.version_snapshot" in sql and "created_at <" in sql:
            (cutoff,) = args
            matches = [
                row for row in self._rows if row["created_at"] < cutoff
            ]
            matches.sort(key=lambda row: (row["note_id"], row["version_index"]))
            return matches

        if "FROM note.version_delta" in sql and "created_at <" in sql:
            (cutoff,) = args
            matches = [
                row for row in self._rows if row["created_at"] < cutoff
            ]
            matches.sort(key=lambda row: (row["note_id"], row["version_index"]))
            return matches

        if "version_index < $2" in sql and "FROM note.version_snapshot" in sql:
            note_id, version_index = args
            matches = [
                row
                for row in self._rows
                if row["note_id"] == note_id
                and row["version_index"] < version_index
            ]
            matches.sort(key=lambda row: row["version_index"], reverse=True)
            return [self._as_entry(row, "snapshot") for row in matches[:1]]

        if "version_index < $2" in sql and "FROM note.version_delta" in sql:
            note_id, version_index = args
            matches = [
                row
                for row in self._rows
                if row["note_id"] == note_id
                and row["version_index"] < version_index
            ]
            matches.sort(key=lambda row: row["version_index"], reverse=True)
            return [self._as_entry(row, "delta") for row in matches[:1]]

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
            if len(args) == 3:
                note_id, snapshot_id, excluded_ids = args
            else:
                note_id, snapshot_id = args
                excluded_ids = []
            matches = [
                row
                for row in self._rows
                if row["note_id"] == note_id
                and row["snapshot_id"] == snapshot_id
                and row["delta_id"] not in excluded_ids
            ]
            return [{"delta_count": len(matches)}]

        return []

    def _as_entry(self, row: dict, kind: str) -> dict:
        """Shape a row like the UNION view used by the version queries."""
        version_id = (
            row["snapshot_id"] if kind == "snapshot" else row["delta_id"]
        )
        return {
            "kind": kind,
            "version_id": version_id,
            "note_id": row["note_id"],
            "version_index": row["version_index"],
            "created_at": row["created_at"],
            "author_id": row.get("author_id"),
            "snapshot_ref": row["snapshot_id"],
        }

    def _delete(self, sql: str, args: tuple):
        if len(args) == 1:
            # DELETE FROM snapshot WHERE snapshot_id = $1
            (snapshot_id,) = args
            before = len(self._rows)
            self._rows[:] = [
                row for row in self._rows if row.get("snapshot_id") != snapshot_id
            ]
            return [{"deleted": before - len(self._rows)}]

        if "created_at >=" in sql:
            # DELETE FROM delta WHERE note_id = $1 AND created_at in [start, end)
            note_id, start, end = args
            before = len(self._rows)
            self._rows[:] = [
                row
                for row in self._rows
                if not (
                    row["note_id"] == note_id
                    and start <= row["created_at"] < end
                )
            ]
            return [{"deleted": before - len(self._rows)}]

        # Legacy scoped delete: note_id, snapshot_id, start, end.
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

    def _update(self, sql: str, args: tuple):
        if "SET snapshot_id" in sql:
            # UPDATE delta SET snapshot_id = $3 ... ANY($2::text[])
            note_id, snapshot_ids, keep_snapshot_id = args
            matches = [
                row
                for row in self._rows
                if row["note_id"] == note_id
                and row["snapshot_id"] in snapshot_ids
            ]
            for row in matches:
                row["snapshot_id"] = keep_snapshot_id
            return matches

        if len(args) == 6:
            # UPDATE snapshot SET version_index/created_at/author/title/content
            snapshot_id, version_index, created_at, author_id, title, content = args
            matches = [
                row for row in self._rows if row.get("snapshot_id") == snapshot_id
            ]
            for row in matches:
                row["version_index"] = version_index
                row["created_at"] = created_at
                row["author_id"] = author_id
                row["title"] = title
                row["content"] = content
            return matches

        if len(args) == 3:
            # UPDATE snapshot SET title/content WHERE snapshot_id = $1
            snapshot_id, title, content = args
            matches = [
                row for row in self._rows if row.get("snapshot_id") == snapshot_id
            ]
            for row in matches:
                row["title"] = title
                row["content"] = content
            return matches

        # Legacy delta patch update: note_id, version_index, patches.
        note_id, version_index, title_patch, content_patch = args
        matches = [
            row
            for row in self._rows
            if row["note_id"] == note_id and row["version_index"] == version_index
        ]
        for row in matches:
            row["title_patch"] = title_patch
            row["content_patch"] = content_patch
        return matches


__all__ = [
    '_FakeTable',
]
