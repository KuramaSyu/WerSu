"""In-memory :class:`ActivityRepoABC` fake for unit tests.

Captures every entity passed to :meth:`add_activity` so tests can
assert the recorded shape.  Read methods return empty lists -- tests
that need richer behaviour (e.g. returning seeded rows) construct
their own repo or set ``added`` directly.
"""

from __future__ import annotations

from typing import List

from src.api.activity import ActivityRepoABC
from src.db.entities.activity import ActivityEntity, ActivityScore, FilterActivity


class _FakeActivityRepo(ActivityRepoABC):
    """In-memory activity repo used by these tests.

    Captures every entity passed to :meth:`add_activity` so each test
    can assert the recorded shape.
    """

    def __init__(self) -> None:
        self.added: List[ActivityEntity] = []

    async def get_activities(self, filter: FilterActivity) -> List[ActivityEntity]:
        return []

    async def get_most_used(self, filter: FilterActivity) -> List[ActivityScore]:
        return []

    async def add_activity(self, activity: ActivityEntity) -> ActivityEntity:
        self.added.append(activity)
        return activity

    async def remove_activity_by_id(self, activity_id: str) -> None:
        self.added = [a for a in self.added if a.id != activity_id]

    async def edit_activity(self, activity: ActivityEntity) -> ActivityEntity:
        for i, existing in enumerate(self.added):
            if existing.id == activity.id:
                self.added[i] = activity
                return activity
        raise ValueError(f"activity not found: {activity.id}")