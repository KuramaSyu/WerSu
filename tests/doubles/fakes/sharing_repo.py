from __future__ import annotations


class _FakeTable:
    """Records SQL calls while returning predefined rows."""

    name = "shared"

    def __init__(self, records: list[dict] | None = None) -> None:
        self.records = records or []
        self.last_sql = ""
        self.last_args = ()

    async def fetch(self, sql: str, *args):
        self.last_sql = " ".join(sql.split())
        self.last_args = args
        return self.records


__all__ = [
    '_FakeTable',
]
