"""gRPC adapter for PreferencesServiceABC."""

from __future__ import annotations

import json
import traceback

import grpc
from grpc.aio import ServicerContext

from src.api import LoggingProvider, PreferencesServiceABC
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.services.preferences_service import PreferenceScope
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.proto.preferences_pb2 import (
    DeletePreferencesRequest,
    DeletePreferencesResponse,
    GetPreferencesRequest,
    GetPreferencesResponse,
    PatchPreferencesRequest,
    PostPreferencesRequest,
    PreferenceScope as GrpcPreferenceScope,
)
from src.grpc_mod.proto.preferences_pb2_grpc import PreferencesServiceServicer


_SCOPE_TO_GRPC = {
    "user": GrpcPreferenceScope.PREFERENCE_SCOPE_USER,
    "shelf": GrpcPreferenceScope.PREFERENCE_SCOPE_SHELF,
    "role": GrpcPreferenceScope.PREFERENCE_SCOPE_ROLE,
}
_GRPC_TO_SCOPE = {v: k for k, v in _SCOPE_TO_GRPC.items()}


class GrpcPreferencesService(PreferencesServiceServicer):
    """Implements the gRPC service defined in preferences.proto."""

    def __init__(
        self,
        preferences_service: PreferencesServiceABC,
        log: LoggingProvider,
        context_factory: ContextFactory[UserContextABC],
    ):
        self._service = preferences_service
        self.log = log(__name__, self)
        self._context = context_factory

    @log_service_call()
    async def GetPreferences(
        self, request: GetPreferencesRequest, context: ServicerContext
    ) -> GetPreferencesResponse:
        try:
            scope = self._decode_scope(request.scope, context)
            self._require_user_id(request.user_id, context)
            blob = await self._service.get_preferences(
                scope, request.target_id,
                await self._context.create(request.user_id),
            )
            return GetPreferencesResponse(preferences_json=_encode(blob))
        except _GrpcError as exc:
            self._set_error(context, exc)
            return GetPreferencesResponse()
        except Exception:
            self.log.error(f"Error reading preferences: {traceback.format_exc()}")
            self._set_error(context, _GrpcError(grpc.StatusCode.INTERNAL, "internal error"))
            return GetPreferencesResponse()

    @log_service_call()
    async def PostPreferences(
        self, request: PostPreferencesRequest, context: ServicerContext
    ) -> GetPreferencesResponse:
        try:
            scope = self._decode_scope(request.scope, context)
            self._require_user_id(request.user_id, context)
            blob = await self._service.update_preferences(
                scope, request.target_id, request.preferences_json,
                await self._context.create(request.user_id),
            )
            return GetPreferencesResponse(preferences_json=_encode(blob))
        except _GrpcError as exc:
            self._set_error(context, exc)
            return GetPreferencesResponse()
        except ValueError as exc:
            self._set_error(context, _GrpcError(grpc.StatusCode.INVALID_ARGUMENT, str(exc)))
            return GetPreferencesResponse()
        except Exception:
            self.log.error(f"Error writing preferences: {traceback.format_exc()}")
            self._set_error(context, _GrpcError(grpc.StatusCode.INTERNAL, "internal error"))
            return GetPreferencesResponse()

    @log_service_call()
    async def PatchPreferences(
        self, request: PatchPreferencesRequest, context: ServicerContext
    ) -> GetPreferencesResponse:
        try:
            scope = self._decode_scope(request.scope, context)
            self._require_user_id(request.user_id, context)
            blob = await self._service.patch_preferences(
                scope, request.target_id, request.preferences_json,
                await self._context.create(request.user_id),
            )
            return GetPreferencesResponse(preferences_json=_encode(blob))
        except _GrpcError as exc:
            self._set_error(context, exc)
            return GetPreferencesResponse()
        except ValueError as exc:
            self._set_error(context, _GrpcError(grpc.StatusCode.INVALID_ARGUMENT, str(exc)))
            return GetPreferencesResponse()
        except Exception:
            self.log.error(f"Error patching preferences: {traceback.format_exc()}")
            self._set_error(context, _GrpcError(grpc.StatusCode.INTERNAL, "internal error"))
            return GetPreferencesResponse()

    @log_service_call()
    async def DeletePreferences(
        self, request: DeletePreferencesRequest, context: ServicerContext
    ) -> DeletePreferencesResponse:
        try:
            scope = self._decode_scope(request.scope, context)
            self._require_user_id(request.user_id, context)
            removed = await self._service.delete_preferences(
                scope, request.target_id,
                await self._context.create(request.user_id),
            )
            return DeletePreferencesResponse(success=removed)
        except _GrpcError as exc:
            self._set_error(context, exc)
            return DeletePreferencesResponse()
        except Exception:
            self.log.error(f"Error deleting preferences: {traceback.format_exc()}")
            self._set_error(context, _GrpcError(grpc.StatusCode.INTERNAL, "internal error"))
            return DeletePreferencesResponse()

    @staticmethod
    def _decode_scope(
        grpc_scope: GrpcPreferenceScope, context: ServicerContext
    ) -> PreferenceScope:
        """Translate the wire enum to the service-layer literal."""
        if grpc_scope in _GRPC_TO_SCOPE:
            return _GRPC_TO_SCOPE[grpc_scope]  # type: ignore[return-value]
        raise _GrpcError(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"unsupported preference scope: {grpc_scope!r}",
        )

    @staticmethod
    def _require_user_id(user_id: str, context: ServicerContext) -> None:
        if not user_id:
            raise _GrpcError(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")

    @staticmethod
    def _set_error(context: ServicerContext, exc: "_GrpcError") -> None:
        context.set_code(exc.code)
        context.set_details(exc.message)


class _GrpcError(Exception):
    """Internal exception used to short-circuit a call with a gRPC status."""

    def __init__(self, code: grpc.StatusCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _encode(blob: dict) -> str:
    """Serialise the dict back to a JSON object string."""
    return json.dumps(blob, separators=(",", ":"))


__all__ = ["GrpcPreferencesService"]
