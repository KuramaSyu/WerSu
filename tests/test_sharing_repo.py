from datetime import datetime

from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.repos.sharing_repo import SharingPostgresRepo


class _UserContext(UserContextABC):
    """Small user context for repo tests."""

    def __init__(self, user_id: str = "actor") -> None:
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id


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


def _share_record(**overrides) -> dict:
    """Build a complete row shaped like the shared table."""
    row = {
        "id": "share-1",
        "description": None,
        "note_id": "note-1",
        "created_at": datetime(2026, 1, 1),
        "created_by": "creator-1",
        "online_since": datetime(2026, 1, 1),
        "online_until": datetime(2026, 12, 31),
        "access_as": "access-user",
    }
    row.update(overrides)
    return row


async def test_get_shares_filters_exact_fields_with_parameterized_sql() -> None:
    """Exact filters should become equality checks with bound values."""
    table = _FakeTable(records=[_share_record()])
    repo = SharingPostgresRepo(table)
    filter = FilterShareNote(
        note_id="note-1",
        created_by="creator-1",
        access_as="access-user",
    )

    shares = await repo.get_shares(filter, _UserContext())

    assert shares == [NoteShareEntity(**_share_record())]
    assert "note_id = $1" in table.last_sql
    assert "created_by = $2" in table.last_sql
    assert "access_as = $3" in table.last_sql
    assert table.last_args == ("note-1", "creator-1", "access-user")


async def test_get_shares_filters_online_since_and_until_ranges() -> None:
    """Date filters use the inclusive comparisons from the sharing contract."""
    table = _FakeTable()
    repo = SharingPostgresRepo(table)
    since = datetime(2026, 1, 1)
    until = datetime(2026, 12, 31)

    await repo.get_shares(
        FilterShareNote(online_since=since, online_until=until),
        _UserContext(),
    )

    assert "online_since >= $1" in table.last_sql
    assert "online_until <= $2" in table.last_sql
    assert table.last_args == (since, until)


async def test_get_shares_filters_explicit_null_dates() -> None:
    """Explicit None is a filter for NULL, unlike UNDEFINED which is ignored."""
    table = _FakeTable()
    repo = SharingPostgresRepo(table)

    await repo.get_shares(
        FilterShareNote(online_since=None, online_until=None),
        _UserContext(),
    )

    assert "online_since IS NULL" in table.last_sql
    assert "online_until IS NULL" in table.last_sql
    assert table.last_args == ()


async def test_get_shares_without_filter_fetches_all_rows() -> None:
    """An all-UNDEFINED filter should not add accidental WHERE clauses."""
    table = _FakeTable(records=[_share_record()])
    repo = SharingPostgresRepo(table)

    shares = await repo.get_shares(FilterShareNote(), _UserContext())

    assert shares == [NoteShareEntity(**_share_record())]
    assert "WHERE TRUE" in table.last_sql
    assert table.last_args == ()
