"""gRPC adapter for :class:`CalculatedStatsServiceABC`.

Thin read-only adapter: it creates the caller context, forwards the
requested target user id (or ``None`` for "me") to the service, and
renders the stored row as a ``CalculatedStats`` proto message.
"""

from __future__ import annotations

import datetime as _dt
import traceback
from typing import Optional

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from grpc.aio import ServicerContext

from src.api.other.types import LoggingProvider
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.services.calculated_stats_service import CalculatedStatsServiceABC
from src.db.entities.calculated_stats import CalculatedStatsEntity
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.proto.calculated_stats_pb2 import (
    CalculatedStats,
    GetCalculatedStatsRequest,
    GetCalculatedStatsResponse,
)
from src.grpc_mod.proto.calculated_stats_pb2_grpc import (
    CalculatedStatsServiceServicer,
)


class GrpcCalculatedStatsService(CalculatedStatsServiceServicer):
    """gRPC adapter for precomputed per-user stats.

    Args:
        stats_service: service that owns storage + recalculation.
        log: logger factory used by the per-call timing decorator.
        context_factory: builds the request
            :class:`~src.api.other.user_context.UserContextABC` from
            ``request.user_id``.
    """

    def __init__(
        self,
        stats_service: CalculatedStatsServiceABC,
        log: LoggingProvider,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._stats_service = stats_service
        self.log = log(__name__, self)
        self._context = context_factory

    @log_service_call()
    async def GetCalculatedStats(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        request: GetCalculatedStatsRequest,
        context: ServicerContext,
    ) -> GetCalculatedStatsResponse:
        try:
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return GetCalculatedStatsResponse()

            actor = await self._context.create(request.user_id)
            target_user_id: Optional[str] = None
            if request.HasField("target_user_id") and request.target_user_id:
                target_user_id = request.target_user_id

            stats = await self._stats_service.get_stats(
                actor, user_id=target_user_id
            )
            if stats is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(
                    "calculated stats not available for user "
                    f"{target_user_id or request.user_id}"
                )
                return GetCalculatedStatsResponse()

            return GetCalculatedStatsResponse(stats=self._to_proto(stats))
        except PermissionError as exc:
            self.log.warning(
                f"Permission denied in GetCalculatedStats: {exc}"
            )
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(exc))
            return GetCalculatedStatsResponse()
        except ValueError as exc:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return GetCalculatedStatsResponse()
        except Exception:
            self.log.error(
                f"Error in GetCalculatedStats: {traceback.format_exc()}"
            )
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(
                "Internal server error while fetching calculated stats"
            )
            return GetCalculatedStatsResponse()

    @staticmethod
    def _to_proto(entity: CalculatedStatsEntity) -> CalculatedStats:
        """Render a stored stats row as the proto message."""
        calculated_at = Timestamp()
        if isinstance(entity.calculated_at, _dt.datetime):
            calculated_at.FromDatetime(entity.calculated_at)

        return CalculatedStats(
            user_id=str(entity.user_id or ""),
            total_chars=int(entity.total_chars or 0),
            total_notes=int(entity.total_notes or 0),
            total_chapters=int(entity.total_chapters or 0),
            total_books=int(entity.total_books or 0),
            calculated_at=calculated_at,
        )


__all__ = ["GrpcCalculatedStatsService"]
