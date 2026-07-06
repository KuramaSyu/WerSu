"""gRPC adapter for :class:`ActivityStatisticsServiceABC`.

Thin layer that converts protobuf payloads into kwargs for the
statistics service, delegates the read query, and converts each
result row back to a protobuf message via the injected
:class:`ConvertToGrpcVisitor`.

Every request carries ``user_id``; the statistics service uses it to
gate view permissions and to resolve "all directories the actor can
view" when neither ``note_id`` nor ``directory_id`` is supplied.
"""

from __future__ import annotations

import traceback
from typing import AsyncIterator, List, Optional

import grpc
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.activity_statistics_service import ActivityStatisticsServiceABC
from src.api.user_context import ContextFactory, UserContextABC
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.activity_pb2 import (
    ACCESSED_AS_SYSTEM,
    ACCESSED_AS_UNSPECIFIED,
    ACCESSED_AS_USER,
    MOST_USED_ALGORITHM_COUNT,
    MOST_USED_ALGORITHM_LOG_COUNT,
    MOST_USED_ALGORITHM_UNSPECIFIED,
    Activity,
    ActivityScore as GrpcActivityScore,
)
from src.grpc_mod.proto.activity_pb2_grpc import (
    ActivityStatisticsServiceServicer,
)
from src.grpc_mod.service import log_service_call


_ALGORITHM_TO_PROTO = {
    "count": MOST_USED_ALGORITHM_COUNT,
    "log_count": MOST_USED_ALGORITHM_LOG_COUNT,
}


def _proto_algorithm_to_str(value: int) -> str:
    """Translate the proto algorithm enum to the service kwarg string."""
    if value == MOST_USED_ALGORITHM_COUNT:
        return "count"
    if value == MOST_USED_ALGORITHM_LOG_COUNT:
        return "log_count"
    return "count"


def _proto_accessed_as_to_str(value: int) -> Optional[str]:
    """Translate the proto ``AccessedAs`` enum to the literal string."""
    if value == ACCESSED_AS_USER:
        return "user"
    if value == ACCESSED_AS_SYSTEM:
        return "system"
    return None


class GrpcActivityStatisticsService(ActivityStatisticsServiceServicer):
    """gRPC adapter for the activity-statistics read service.

    Args:
        statistics_service: the service layer that does the real work.
        log: logger factory used for the per-call timing decorator.
        to_grpc: visitor that turns each
            :class:`~src.db.entities.activity.ActivityEntity` /
            :class:`~src.db.entities.activity.ActivityScore` into its
            gRPC counterpart.
        context_factory: factory that builds the request
            :class:`~src.api.user_context.UserContextABC` from
            ``request.user_id``.
    """

    def __init__(
        self,
        statistics_service: ActivityStatisticsServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._statistics_service = statistics_service
        self.log = log(__name__, self)
        self._to_grpc = to_grpc
        self._context = context_factory

    @log_service_call()
    async def GetActivityHistory(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        request,  # GetActivityHistoryRequest
        context: ServicerContext,
    ) -> AsyncIterator[Activity]:
        """Stream the activity log for everything the actor can view."""
        try:
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return

            actor = await self._context.create(request.user_id)
            filter_kwargs = self._filter_kwargs(request.filter)
            # ``unique_per_day`` is aggregation-only; the history
            # service doesn't accept it.
            filter_kwargs.pop("unique_per_day", None)

            rows = await self._statistics_service.get_history(
                actor, **filter_kwargs,
            )
            for row in rows:
                yield row.convert(self._to_grpc)
        except PermissionError as exc:
            self.log.warning(f"Permission denied in GetActivityHistory: {exc}")
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(exc))
            return
        except Exception:
            self.log.error(
                f"Error in GetActivityHistory: {traceback.format_exc()}"
            )
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(
                "Internal server error while fetching activity history"
            )
            return

    @log_service_call()
    async def GetMostUsedActivity(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        request,  # GetMostUsedActivityRequest
        context: ServicerContext,
    ) -> AsyncIterator[GrpcActivityScore]:
        """Stream aggregate note scores for everything the actor can view."""
        try:
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return

            actor = await self._context.create(request.user_id)
            filter_kwargs = self._filter_kwargs(request.filter)

            algorithm = _proto_algorithm_to_str(request.algorithm)
            limit = request.limit if request.HasField("limit") else None

            rows = await self._statistics_service.get_most_used(
                actor, algorithm=algorithm, limit=limit, **filter_kwargs,
            )
            for row in rows:
                yield row.convert(self._to_grpc)
        except PermissionError as exc:
            self.log.warning(f"Permission denied in GetMostUsedActivity: {exc}")
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(exc))
            return
        except Exception:
            self.log.error(
                f"Error in GetMostUsedActivity: {traceback.format_exc()}"
            )
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(
                "Internal server error while fetching most-used activity"
            )
            return

    def _filter_kwargs(self, filter) -> dict:
        """Translate an ``ActivityFilter`` proto into service kwargs.

        Empty string fields are treated as "not set" so callers can
        send a partial filter without zeroing out the column.
        ``unique_per_day`` is hoisted out of the filter and only
        applies to the most-used path -- the history service ignores
        it.
        """
        kwargs = {
            "note_id": _present(filter.note_id),
            "directory_id": _present(filter.directory_id),
            "actor_id": _present(filter.actor_id),
            "role_id": _present(filter.role_id),
            "accessed_as": _proto_accessed_as_to_str(filter.accessed_as),
            "days": filter.days if filter.HasField("days") else None,
            "limit": filter.limit if filter.HasField("limit") else None,
            "offset": filter.offset if filter.HasField("offset") else None,
        }
        actions: List[str] = [a for a in filter.actions if a]
        if actions:
            kwargs["actions"] = actions
        if filter.HasField("unique_per_day"):
            kwargs["unique_per_day"] = bool(filter.unique_per_day)
        return {k: v for k, v in kwargs.items() if v is not None or _is_zero(v)}


def _present(value: str) -> Optional[str]:
    """Return ``value`` if non-empty, else ``None``."""
    return value if value else None


def _is_zero(value) -> bool:
    """Treat numeric ``0`` as a present value (rather than dropping it)."""
    return value == 0


__all__ = [
    "GrpcActivityStatisticsService",
    "_ALGORITHM_TO_PROTO",
]