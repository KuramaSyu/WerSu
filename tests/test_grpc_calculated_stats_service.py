"""Unit tests for :class:`GrpcCalculatedStatsService`."""

from __future__ import annotations

import datetime as _dt
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from src.db.entities.calculated_stats import CalculatedStatsEntity
from src.grpc_mod.calculated_stats_service import GrpcCalculatedStatsService
from src.grpc_mod.proto.calculated_stats_pb2 import (
    GetCalculatedStatsRequest,
)
from tests.doubles import _DummyUserContext


@pytest.fixture
def stats_service() -> MagicMock:
    svc = MagicMock()
    svc.get_stats = AsyncMock(return_value=None)
    return svc


@pytest.fixture
def context_factory() -> MagicMock:
    factory = MagicMock()
    factory.create = AsyncMock(return_value=_DummyUserContext(user_id="u1"))
    return factory


@pytest.fixture
def grpc_service(
    stats_service: MagicMock,
    context_factory: MagicMock,
) -> GrpcCalculatedStatsService:
    log = MagicMock()
    log.return_value = MagicMock()
    return GrpcCalculatedStatsService(
        stats_service=stats_service,
        log=log,
        context_factory=context_factory,
    )


class TestGetCalculatedStats:
    async def test_requires_user_id(
        self, grpc_service: GrpcCalculatedStatsService
    ) -> None:
        ctx = MagicMock()
        response = await grpc_service.GetCalculatedStats(
            GetCalculatedStatsRequest(user_id=""), ctx
        )

        assert response.stats.user_id == ""
        ctx.set_code.assert_called_with(grpc.StatusCode.INVALID_ARGUMENT)

    async def test_returns_not_found_when_no_row(
        self, grpc_service: GrpcCalculatedStatsService
    ) -> None:
        ctx = MagicMock()
        response = await grpc_service.GetCalculatedStats(
            GetCalculatedStatsRequest(user_id="u1"), ctx
        )

        assert response.stats.user_id == ""
        ctx.set_code.assert_called_with(grpc.StatusCode.NOT_FOUND)

    async def test_renders_entity_and_forwards_target_user(
        self,
        grpc_service: GrpcCalculatedStatsService,
        stats_service: MagicMock,
    ) -> None:
        stats_service.get_stats.return_value = CalculatedStatsEntity(
            user_id="u2",
            total_chars=12,
            total_notes=2,
            total_chapters=1,
            total_books=3,
            calculated_at=_dt.datetime(
                2026, 1, 1, tzinfo=_dt.timezone.utc
            ),
        )
        ctx = MagicMock()
        response = await grpc_service.GetCalculatedStats(
            GetCalculatedStatsRequest(
                user_id="u1", target_user_id="u2"
            ),
            ctx,
        )

        assert response.stats.user_id == "u2"
        assert response.stats.total_chars == 12
        assert response.stats.total_notes == 2
        assert response.stats.total_chapters == 1
        assert response.stats.total_books == 3
        assert response.stats.calculated_at.seconds == 1767225600
        _, kwargs = stats_service.get_stats.await_args
        assert kwargs["user_id"] == "u2"

    async def test_permission_error_maps_to_permission_denied(
        self,
        grpc_service: GrpcCalculatedStatsService,
        stats_service: MagicMock,
    ) -> None:
        stats_service.get_stats.side_effect = PermissionError("nope")
        ctx = MagicMock()
        response = await grpc_service.GetCalculatedStats(
            GetCalculatedStatsRequest(user_id="u1"), ctx
        )

        assert response.stats.user_id == ""
        ctx.set_code.assert_called_with(grpc.StatusCode.PERMISSION_DENIED)
