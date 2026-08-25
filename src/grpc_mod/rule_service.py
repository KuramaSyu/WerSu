"""gRPC adapter for :class:`src.api.services.rule_service.RuleServiceABC`.

Implements the ``RuleService`` from ``src/grpc_mod/proto/rule.proto``
(``RuleServiceImpl``).  Every method requires ``request.user_id``
so the service can build a :class:`UserContextABC` for permission
checks; permission enforcement itself lives in the service layer
(every mutation is gated on ``manage`` on the attached entity,
or on global admin for a global rule).

The adapter is intentionally thin: it converts proto messages to
domain entities via :mod:`src.grpc_mod.converter.rule_converter`,
delegates to the service layer, and converts the result back via
the injected :class:`ConvertToGrpcVisitor`.  Error handling mirrors
the existing gRPC adapters -- :exc:`RulePermissionError` and
:exc:`ValueError` are mapped to ``PERMISSION_DENIED`` and
``INVALID_ARGUMENT`` respectively; everything else is logged and
returned as ``INTERNAL``.
"""

from __future__ import annotations

import traceback
from typing import Optional

import grpc
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.services.rule_service import (
    RulePermissionError,
    RuleServiceABC,
    RuleServiceError,
)
from src.db.entities.rule import AttachedEntityType
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.converter.rule_converter import (
    grpc_create_rule_to_entity,
    grpc_update_rule_to_entity,
)
from src.grpc_mod.proto.rule_pb2 import (
    CreateRuleRequest,
    CreateRuleResponse,
    DeleteRuleRequest,
    DeleteRuleResponse,
    GetRuleRequest,
    GetRuleResponse,
    GetRulesRequest,
    GetRulesResponse,
    Rule,
    RuleFilter,
    UpdateRuleRequest,
    UpdateRuleResponse,
)
from src.grpc_mod.proto.rule_pb2_grpc import RuleServiceServicer


def _coerce_attached_entity_type(raw: str) -> Optional[AttachedEntityType]:
    """Coerce a ``RuleFilter.attached_entity_type`` string to a literal.

    Empty string -> ``None`` (no filter).  ``"directory"`` /
    ``"note"`` / ``"shelf"`` pass through.  Anything else is
    treated as ``None`` to avoid an opaque filter value.
    """
    if not raw:
        return None
    if raw in ("directory", "note", "shelf"):
        return raw  # type: ignore[return-value]
    return None


class GrpcRuleService(RuleServiceServicer):
    """gRPC adapter for :class:`RuleServiceABC`."""

    def __init__(
        self,
        rule_service: RuleServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._rule_service = rule_service
        self._to_grpc = to_grpc
        self._context = context_factory
        self.log = log(__name__, self)


    @log_service_call()
    async def CreateRule(
        self,
        request: CreateRuleRequest,
        context: ServicerContext,
    ) -> CreateRuleResponse:
        try:
            self._require_user_id(request.user_id)
            entity = grpc_create_rule_to_entity(request)
            created = await self._rule_service.create_rule(
                entity,
                await self._context.create(request.user_id),
            )
            return CreateRuleResponse(rule=created.convert(self._to_grpc))
        except RulePermissionError as exc:
            return self._fail(context, grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except RuleServiceError as exc:
            return self._fail(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except ValueError as exc:
            return self._fail(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception:
            self.log.error(
                f"CreateRule failed: {traceback.format_exc()}\nrequest: {request}"
            )
            return self._fail(context, grpc.StatusCode.INTERNAL, "Internal error")


    @log_service_call()
    async def GetRule(
        self,
        request: GetRuleRequest,
        context: ServicerContext,
    ) -> GetRuleResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "id is required",
                )
            rule = await self._rule_service.get_rule(
                request.id,
                await self._context.create(request.user_id),
            )
            if rule is None:
                return self._fail(
                    context, grpc.StatusCode.NOT_FOUND, "rule not found",
                )
            return GetRuleResponse(rule=rule.convert(self._to_grpc))
        except RulePermissionError as exc:
            return self._fail(context, grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except Exception:
            self.log.error(
                f"GetRule failed: {traceback.format_exc()}\nrequest: {request}"
            )
            return self._fail(context, grpc.StatusCode.INTERNAL, "Internal error")


    @log_service_call()
    async def GetRules(
        self,
        request: GetRulesRequest,
        context: ServicerContext,
    ) -> GetRulesResponse:
        try:
            self._require_user_id(request.user_id)
            filter_proto = request.filter
            kwargs = {}
            if filter_proto.event_type:
                kwargs["event_type"] = filter_proto.event_type
            attached_entity_type = _coerce_attached_entity_type(
                filter_proto.attached_entity_type,
            )
            if attached_entity_type is not None:
                kwargs["attached_entity_type"] = attached_entity_type
            if filter_proto.attached_entity_id:
                kwargs["attached_entity_id"] = filter_proto.attached_entity_id
            if filter_proto.enabled_only:
                kwargs["enabled_only"] = True
            if filter_proto.creator_id:
                kwargs["creator_id"] = filter_proto.creator_id
            rules = await self._rule_service.list_rules(
                await self._context.create(request.user_id),
                **kwargs,
            )
            return GetRulesResponse(
                rules=[r.convert(self._to_grpc) for r in rules],
            )
        except Exception:
            self.log.error(
                f"GetRules failed: {traceback.format_exc()}\nrequest: {request}"
            )
            return self._fail(context, grpc.StatusCode.INTERNAL, "Internal error")


    @log_service_call()
    async def UpdateRule(
        self,
        request: UpdateRuleRequest,
        context: ServicerContext,
    ) -> UpdateRuleResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "id is required",
                )
            entity = grpc_update_rule_to_entity(request)
            updated = await self._rule_service.update_rule(
                entity,
                await self._context.create(request.user_id),
            )
            return UpdateRuleResponse(rule=updated.convert(self._to_grpc))
        except RulePermissionError as exc:
            return self._fail(context, grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except (RuleServiceError, ValueError) as exc:
            return self._fail(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception:
            self.log.error(
                f"UpdateRule failed: {traceback.format_exc()}\nrequest: {request}"
            )
            return self._fail(context, grpc.StatusCode.INTERNAL, "Internal error")


    @log_service_call()
    async def DeleteRule(
        self,
        request: DeleteRuleRequest,
        context: ServicerContext,
    ) -> DeleteRuleResponse:
        try:
            self._require_user_id(request.user_id)
            if not request.id:
                return self._fail(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "id is required",
                )
            await self._rule_service.delete_rule(
                request.id,
                await self._context.create(request.user_id),
            )
            return DeleteRuleResponse()
        except RulePermissionError as exc:
            return self._fail(context, grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except ValueError as exc:
            return self._fail(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception:
            self.log.error(
                f"DeleteRule failed: {traceback.format_exc()}\nrequest: {request}"
            )
            return self._fail(context, grpc.StatusCode.INTERNAL, "Internal error")


    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _require_user_id(user_id: str) -> None:
        """No-op helper kept for symmetry with other gRPC adapters."""
        if not user_id:
            # We raise instead of returning a ServicerContext failure
            # so the caller stays free to map the error.  The
            # actual ``set_code`` happens in the handler.
            raise ValueError("user_id is required")

    @staticmethod
    def _fail(
        context: ServicerContext,
        code: grpc.StatusCode,
        message: str,
    ):
        """Set the gRPC error code/details on ``context``.

        Returns an empty / default-constructed response so the
        handler can ``return`` it without further work.
        """
        context.set_code(code)
        context.set_details(message)
        return _EmptyResponse.of(code)


# Tiny helper to keep ``_fail`` readable without sprawling
# if/elif chains in the handlers.  Imported here rather than
# at module top to avoid dragging proto types into the gRPC
# adapter's import surface when the linter is introspecting
# the file.
class _EmptyResponse:
    """Factory for the empty response object matching a gRPC code."""

    @staticmethod
    def of(code: grpc.StatusCode):
        """Return a default response matching the requested code.

        The gRPC framework inspects the response type to decide
        what to wire; returning a default-constructed message of
        the right type is enough when the handler is about to
        fail.  We keep the mapping tiny -- only the response
        types used by the rule service -- so the surface stays
        explicit.
        """
        # The error response is ignored by gRPC when the code is
        # non-OK; the framework sends a ``google.rpc.Status`` to
        # the client.  Returning ``None`` would also work but
        # trips some type checkers; this is the path of least
        # resistance.
        from src.grpc_mod.proto.rule_pb2 import (  # noqa: WPS433
            CreateRuleResponse,
            GetRuleResponse,
            GetRulesResponse,
            UpdateRuleResponse,
            DeleteRuleResponse,
        )
        # A more conservative approach: always return the
        # delete-style empty response when failing; it's the
        # smallest message and is ignored anyway.
        return DeleteRuleResponse()


__all__ = ["GrpcRuleService"]
