"""In-memory calculated-stats repo double."""

from __future__ import annotations

from src.db.entities.calculated_stats import (
    CalculatedStatsEntity,
    DirectoryAggregate,
    NoteAggregate,
)


class _FakeCalculatedStatsRepo:
    """Minimal stats repo double that records aggregate inputs."""

    def __init__(self) -> None:
        self.rows: dict[str, CalculatedStatsEntity] = {}
        self.aggregated_note_ids: list[str] = []
        self.aggregated_directory_ids: list[str] = []

    async def list_user_ids(self) -> list[str]:
        return ["u1", "u2"]

    async def get(self, user_id: str):
        return self.rows.get(user_id)

    async def fetch_by_user_ids(self, user_ids: list[str]):
        return [self.rows[u] for u in user_ids if u in self.rows]

    async def upsert(self, stats: CalculatedStatsEntity):
        self.rows[str(stats.user_id)] = stats
        return stats

    async def aggregate_notes(self, note_ids: list[str]):
        self.aggregated_note_ids = list(note_ids)
        return NoteAggregate(
            total_notes=len(note_ids),
            total_chars=sum(len(note_id) for note_id in note_ids),
        )

    async def aggregate_directories(self, directory_ids: list[str]):
        self.aggregated_directory_ids = list(directory_ids)
        if not directory_ids:
            return DirectoryAggregate()
        return DirectoryAggregate(total_books=1, total_chapters=1)


__all__ = ["_FakeCalculatedStatsRepo"]
