"""Unit tests for :class:`GrpcActivityStatisticsService`.

The service delegates to an
:class:`ActivityStatisticsServiceABC` mock and converts each result
row to its proto counterpart via the real :class:`ConvertToGrpcVisitor`.
Tests assert both the kwargs forwarded to the service and the proto
payload emitted.
"""

from __future__ import annotations

import datetime as _dt
from typing import List
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from src.db.entities.activity import ActivityEntity, ActivityScore
from src.grpc_mod.activity_statistics_service import GrpcActivityStatisticsService
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.activity_pb2 import (
    ACCESSED_AS_SYSTEM,
    ACCESSED_AS_USER,
    Activity,
    ActivityFilter,
    GetActivityHistoryRequest,
    GetMostUsedActivityRequest,
    MOST_USED_ALGORITHM_COUNT,
    MOST_USED_ALGORITHM_LOG_COUNT,
)
from tests.stubs.user_context import _UserContext


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def stats_service() -> MagicMock:
    """Mock statistics service -- tests set return values per case."""
    svc = MagicMock()
    svc.get_history = AsyncMock(return_value=[])
    svc.get_most_used = AsyncMock(return_value=[])
    return svc


@pytest.fixture
def visitor() -> ConvertToGrpcVisitor:
    return ConvertToGrpcVisitor()


@pytest.fixture
def context_factory() -> MagicMock:
    cf = MagicMock()
    cf.create = AsyncMock(return_value=_UserContext(user_id="alice"))
    return cf


@pytest.fixture
def logging_provider() -> MagicMock:
    log = MagicMock()
    log.return_value = MagicMock()
    return log


@pytest.fixture
def grpc_servicer(
    stats_service: MagicMock,
    logging_provider: MagicMock,
    visitor: ConvertToGrpcVisitor,
    context_factory: MagicMock,
) -> GrpcActivityStatisticsService:
    return GrpcActivityStatisticsService(
        statistics_service=stats_service,
        log=logging_provider,
        to_grpc=visitor,
        context_factory=context_factory,
    )


def _entity(
    *,
    id: str = "a-1",
    actor_id: str = "alice",
    accessed_as: str = "user",
    action: str = "note_viewed",
    note_id: str = "n-1",
    directory_id: str = "",
    role_id: str = "",
    at: _dt.datetime = _dt.datetime(2026, 7, 6, 12, 0, 0),
    metadata: dict | None = None,
) -> ActivityEntity:
    return ActivityEntity(
        id=id,
        actor_id=actor_id,
        accessed_as=accessed_as,  # type: ignore[arg-type]
        action=action,  # type: ignore[arg-type]
        note_id=note_id,
        directory_id=directory_id,
        role_id=role_id,
        at=at,
        metadata=metadata or {},
    )


def _score(note_id: str = "n-1", score: float = 2.0) -> ActivityScore:
    return ActivityScore(note_id=note_id, score=score)


# --------------------------------------------------------------------------
# GetActivityHistory
# --------------------------------------------------------------------------


class TestGetActivityHistory:
    """History streams rows from ``get_history`` through the visitor."""

    @pytest.mark.asyncio
    async def test_user_id_required(
        self, grpc_servicer: GrpcActivityStatisticsService, context_factory: MagicMock,
    ) -> None:
        req = GetActivityHistoryRequest(user_id="")
        ctx = MagicMock()
        async for _ in grpc_servicer.GetActivityHistory(req, ctx):
            pass
        ctx.set_code.assert_called_with(grpc.StatusCode.INVALID_ARGUMENT)
        ctx.set_details.assert_called_with("user_id is required")
        context_factory.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_streams_one_proto_per_row(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        stats_service.get_history.return_value = [
            _entity(id="a-1", note_id="n-1"),
            _entity(id="a-2", note_id="n-2"),
        ]
        req = GetActivityHistoryRequest(
            user_id="alice",
            filter=ActivityFilter(note_id="n-1"),
        )
        ctx = MagicMock()
        rows: List[Activity] = []
        async for r in grpc_servicer.GetActivityHistory(req, ctx):
            rows.append(r)
        assert [r.id for r in rows] == ["a-1", "a-2"]

    @pytest.mark.asyncio
    async def test_filter_kwargs_forwarded(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        f = ActivityFilter(
            note_id="n-1",
            actor_id="alice",
            days=30,
            limit=10,
            offset=5,
        )
        req = GetActivityHistoryRequest(user_id="alice", filter=f)
        ctx = MagicMock()
        async for _ in grpc_servicer.GetActivityHistory(req, ctx):
            pass
        kwargs = stats_service.get_history.call_args.kwargs
        assert kwargs["note_id"] == "n-1"
        assert kwargs["actor_id"] == "alice"
        assert kwargs["days"] == 30
        assert kwargs["limit"] == 10
        assert kwargs["offset"] == 5

    @pytest.mark.asyncio
    async def test_accessed_as_forwarded(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        req = GetActivityHistoryRequest(
            user_id="alice",
            filter=ActivityFilter(accessed_as=ACCESSED_AS_SYSTEM),
        )
        ctx = MagicMock()
        async for _ in grpc_servicer.GetActivityHistory(req, ctx):
            pass
        assert stats_service.get_history.call_args.kwargs["accessed_as"] == "system"

    @pytest.mark.asyncio
    async def test_actions_forwarded(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        req = GetActivityHistoryRequest(
            user_id="alice",
            filter=ActivityFilter(actions=["note_viewed", "note_edited"]),
        )
        ctx = MagicMock()
        async for _ in grpc_servicer.GetActivityHistory(req, ctx):
            pass
        assert list(stats_service.get_history.call_args.kwargs["actions"]) == [
            "note_viewed", "note_edited",
        ]

    @pytest.mark.asyncio
    async def test_unique_per_day_dropped_for_history(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        req = GetActivityHistoryRequest(
            user_id="alice",
            filter=ActivityFilter(unique_per_day=True),
        )
        ctx = MagicMock()
        async for _ in grpc_servicer.GetActivityHistory(req, ctx):
            pass
        assert "unique_per_day" not in stats_service.get_history.call_args.kwargs

    @pytest.mark.asyncio
    async def test_permission_error_returns_permission_denied(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        stats_service.get_history.side_effect = PermissionError("denied")
        req = GetActivityHistoryRequest(user_id="alice", filter=ActivityFilter())
        ctx = MagicMock()
        async for _ in grpc_servicer.GetActivityHistory(req, ctx):
            pass
        ctx.set_code.assert_called_with(grpc.StatusCode.PERMISSION_DENIED)
        ctx.set_details.assert_called_with("denied")

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_internal(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        stats_service.get_history.side_effect = RuntimeError("kaboom")
        req = GetActivityHistoryRequest(user_id="alice", filter=ActivityFilter())
        ctx = MagicMock()
        async for _ in grpc_servicer.GetActivityHistory(req, ctx):
            pass
        ctx.set_code.assert_called_with(grpc.StatusCode.INTERNAL)


# --------------------------------------------------------------------------
# GetMostUsedActivity
# --------------------------------------------------------------------------


class TestGetMostUsedActivity:
    """Most-used streams scores from ``get_most_used``."""

    @pytest.mark.asyncio
    async def test_user_id_required(
        self, grpc_servicer: GrpcActivityStatisticsService,
    ) -> None:
        req = GetMostUsedActivityRequest(user_id="")
        ctx = MagicMock()
        async for _ in grpc_servicer.GetMostUsedActivity(req, ctx):
            pass
        ctx.set_code.assert_called_with(grpc.StatusCode.INVALID_ARGUMENT)

    @pytest.mark.asyncio
    async def test_streams_one_proto_per_score(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        stats_service.get_most_used.return_value = [
            _score(note_id="n-1", score=3.0),
            _score(note_id="n-2", score=1.0),
        ]
        req = GetMostUsedActivityRequest(user_id="alice", filter=ActivityFilter())
        ctx = MagicMock()
        scores = []
        async for r in grpc_servicer.GetMostUsedActivity(req, ctx):
            scores.append(r)
        assert [s.note_id for s in scores] == ["n-1", "n-2"]
        assert scores[0].score == 3.0

    @pytest.mark.asyncio
    async def test_algorithm_count_translates(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        req = GetMostUsedActivityRequest(
            user_id="alice", filter=ActivityFilter(),
            algorithm=MOST_USED_ALGORITHM_COUNT,
        )
        ctx = MagicMock()
        async for _ in grpc_servicer.GetMostUsedActivity(req, ctx):
            pass
        assert stats_service.get_most_used.call_args.kwargs["algorithm"] == "count"

    @pytest.mark.asyncio
    async def test_algorithm_log_count_translates(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        req = GetMostUsedActivityRequest(
            user_id="alice", filter=ActivityFilter(),
            algorithm=MOST_USED_ALGORITHM_LOG_COUNT,
        )
        ctx = MagicMock()
        async for _ in grpc_servicer.GetMostUsedActivity(req, ctx):
            pass
        assert stats_service.get_most_used.call_args.kwargs["algorithm"] == "log_count"

    @pytest.mark.asyncio
    async def test_unique_per_day_forwarded(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        req = GetMostUsedActivityRequest(
            user_id="alice",
            filter=ActivityFilter(unique_per_day=True),
        )
        ctx = MagicMock()
        async for _ in grpc_servicer.GetMostUsedActivity(req, ctx):
            pass
        assert stats_service.get_most_used.call_args.kwargs["unique_per_day"] is True

    @pytest.mark.asyncio
    async def test_limit_forwarded_when_set(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        req = GetMostUsedActivityRequest(
            user_id="alice", filter=ActivityFilter(),
            limit=50,
        )
        ctx = MagicMock()
        async for _ in grpc_servicer.GetMostUsedActivity(req, ctx):
            pass
        assert stats_service.get_most_used.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_permission_error_returns_permission_denied(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        stats_service.get_most_used.side_effect = PermissionError("denied")
        req = GetMostUsedActivityRequest(user_id="alice", filter=ActivityFilter())
        ctx = MagicMock()
        async for _ in grpc_servicer.GetMostUsedActivity(req, ctx):
            pass
        ctx.set_code.assert_called_with(grpc.StatusCode.PERMISSION_DENIED)


# --------------------------------------------------------------------------
# Visitor-level conversion sanity
# --------------------------------------------------------------------------


class TestVisitorConversion:
    """The visitor + adapter chain produces correct proto messages."""

    @pytest.mark.asyncio
    async def test_activity_proto_carries_metadata_json(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        import json
        stats_service.get_history.return_value = [
            _entity(metadata={"from": "v1", "to": "v2"}),
        ]
        req = GetActivityHistoryRequest(user_id="alice", filter=ActivityFilter())
        ctx = MagicMock()
        rows = []
        async for r in grpc_servicer.GetActivityHistory(req, ctx):
            rows.append(r)
        assert json.loads(rows[0].metadata_json) == {"from": "v1", "to": "v2"}

    @pytest.mark.asyncio
    async def test_metadata_merges_with_enrichment_without_overriding(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        """Pre-existing metadata keys survive the enrichment stamp.

        The statistics service stamps ``note_title`` /
        ``note_stripped_content`` onto single-note history rows;
        the visitor must merge those on top of any metadata the
        caller already wrote without clobbering the caller's keys.
        Keys that the visitor injects (``note_title`` /
        ``note_stripped_content``) are still present in the merged
        payload.
        """
        import json
        row = _entity(
            metadata={"from": "v1", "to": "v2", "permission": "read"},
        )
        row.note_title = "Note One"
        row.note_stripped_content = "hello world"
        stats_service.get_history.return_value = [row]
        req = GetActivityHistoryRequest(user_id="alice", filter=ActivityFilter())
        ctx = MagicMock()
        rows = []
        async for r in grpc_servicer.GetActivityHistory(req, ctx):
            rows.append(r)
        merged = json.loads(rows[0].metadata_json)
        # caller's keys survive unchanged
        assert merged["from"] == "v1"
        assert merged["to"] == "v2"
        assert merged["permission"] == "read"
        # enrichment fields ride along
        assert merged["note_title"] == "Note One"
        assert merged["note_stripped_content"] == "hello world"

    @pytest.mark.asyncio
    async def test_metadata_enrichment_only_overrides_when_set(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        """A caller-written ``note_title`` is not clobbered by the enrichment.

        If the activity row's metadata already carries a ``note_title``
        key but the service did not stamp an enrichment (e.g. the
        filter had no note_id pin), the visitor leaves the metadata
        untouched.
        """
        import json
        row = _entity(metadata={"note_title": "Original"})
        # neither enrichment field set -> nothing stamped
        stats_service.get_history.return_value = [row]
        req = GetActivityHistoryRequest(user_id="alice", filter=ActivityFilter())
        ctx = MagicMock()
        rows = []
        async for r in grpc_servicer.GetActivityHistory(req, ctx):
            rows.append(r)
        assert json.loads(rows[0].metadata_json) == {"note_title": "Original"}

    @pytest.mark.asyncio
    async def test_activity_score_proto_carries_title_and_stripped_content(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        """Most-used scores forward title and stripped content as direct fields."""
        stats_service.get_most_used.return_value = [
            ActivityScore(
                note_id="n-1",
                score=3.0,
                title="Note One",
                stripped_content="hello world",
            ),
        ]
        req = GetMostUsedActivityRequest(user_id="alice", filter=ActivityFilter())
        ctx = MagicMock()
        scores = []
        async for r in grpc_servicer.GetMostUsedActivity(req, ctx):
            scores.append(r)
        assert scores[0].note_id == "n-1"
        assert scores[0].score == 3.0
        assert scores[0].title == "Note One"
        assert scores[0].stripped_content == "hello world"

    @pytest.mark.asyncio
    async def test_accessed_as_user_proto_value(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        stats_service.get_history.return_value = [_entity(accessed_as="user")]
        req = GetActivityHistoryRequest(user_id="alice", filter=ActivityFilter())
        ctx = MagicMock()
        rows = []
        async for r in grpc_servicer.GetActivityHistory(req, ctx):
            rows.append(r)
        assert rows[0].accessed_as == ACCESSED_AS_USER

    @pytest.mark.asyncio
    async def test_accessed_as_system_proto_value(
        self, grpc_servicer: GrpcActivityStatisticsService,
        stats_service: MagicMock,
    ) -> None:
        stats_service.get_history.return_value = [_entity(accessed_as="system")]
        req = GetActivityHistoryRequest(user_id="alice", filter=ActivityFilter())
        ctx = MagicMock()
        rows = []
        async for r in grpc_servicer.GetActivityHistory(req, ctx):
            rows.append(r)
        assert rows[0].accessed_as == ACCESSED_AS_SYSTEM