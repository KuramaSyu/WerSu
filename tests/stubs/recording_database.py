"""Recording DatabaseABC fake for unit tests.

Records every execute / fetchrow call and replays queued responses
so repos that run raw SQL can be exercised without booting
Postgres.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.db.database import DatabaseABC


class _RecordingDatabase(DatabaseABC):
    """In-memory DatabaseABC that records every call.

    Reads return queued rows; writes record the exact SQL string
    and parameter tuple so assertions can verify both without
    having to parse SQL.
    """

    def __init__(
        self,
        fetchrow_responses: Optional[List[Optional[Dict[str, Any]]]] = None,
        execute_response: str = "DELETE 0",
    ) -> None:
        self._fetchrow_responses = list(fetchrow_responses or [])
        self.fetchrow_calls: List[Tuple[str, tuple]] = []
        self.execute_calls: List[Tuple[str, tuple]] = []
        self.execute_response: str = execute_response
        self.execute_call_count: int = 0

    async def init_db(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @property
    def pool(self):
        raise NotImplementedError

    async def execute(self, query: str, *args) -> str:
        self.execute_calls.append((query, args))
        self.execute_call_count += 1
        return self.execute_response

    async def fetch(self, query: str, *args):
        raise NotImplementedError(
            "_RecordingDatabase.fetch is not implemented; queue a fetchrow response instead"
        )

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        if not self._fetchrow_responses:
            raise AssertionError(
                "_RecordingDatabase.fetchrow called without a queued response"
            )
        return self._fetchrow_responses.pop(0)


__all__ = ["_RecordingDatabase"]
