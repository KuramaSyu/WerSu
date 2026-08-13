"""gRPC adapter for :class:`src.api.services.role.RoleServiceABC`.

Implements the ``RoleService`` from ``src/grpc_mod/proto/role.proto``.
Every write method requires ``request.user_id`` so the service can
build a :class:`UserContextABC` for permission checks; permission
enforcement itself lives in the service layer (every mutation is
gated on ``role#manage`` for that role, except ``create_role`` which
uses the super-admin env-var fallback).
"""

from __future__ import annotations

import traceback
from typing import AsyncIterator

import grpc
from google.protobuf.empty_pb2 import Empty
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.services.role import RoleServiceABC
from src.db.entities.user.role import RoleFilter
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.from_proto import (
    grpc_create_role_to_entity,
    grpc_role_to_domain,
    grpc_update_role_to_entity,
    to_role_filter_entity,
)
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.role_pb2 import (
    AddUserToRoleRequest,
    CreateRoleRequest,
    DeleteRoleRequest,
    GetRoleRequest,
    GetRolesForUserRequest,
    GetRolesRequest,
    GetUsersForRoleRequest,
    RemoveUserFromRoleRequest,
    Role,
    UpdateRoleRequest,
    UserRoleMembership,
)
from src.grpc_mod.proto.role_pb2_grpc import RoleServiceServicer


class GrpcRoleService(RoleServiceServicer):
    """gRPC adapter for the role service."""

    def __init__(
        self,
        role_service: RoleServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._role_service = role_service
        self._to_grpc = to_grpc
        self._context = context_factory
        self.log = log(__name__, self)

    # ---- role CRUD -------------------------------------------------------

    @log_service_call()
    async def CreateRole(
        self,
        request: CreateRoleRequest,
        context: ServicerContext,
    ) -> Role:
        try:
            self._require_user_id(request.user_id)
            entity = grpc_create_role_to_entity(request)
            created = await self._role_service.create_role(
                entity,
                await self._context.create(request.user_id),
            )
            return created.convert(self._to_grpc)
        except Exception as exc:
            return self._handle_role_exception(exc, context)

    @log_service_call()
    async def UpdateRole(
        self,
        request: UpdateRoleRequest,
        context: ServicerContext,
    ) -> Role:
        try:
            self._require_user_id(request.user_id)
            entity = grpc_update_role_to_entity(request)
            updated = await self._role_service.update_role(
                entity,
                await self._context.create(request.user_id),
            )
            return updated.convert(self._to_grpc)
        except Exception as exc:
            return self._handle_role_exception(exc, context)

    @log_service_call()
    async def DeleteRole(
        self,
        request: DeleteRoleRequest,
        context: ServicerContext,
    ) -> Empty:
        try:
            self._require_user_id(request.user_id)
            await self._role_service.delete_role(
                request.id,
                await self._context.create(request.user_id),
            )
            return Empty()
        except Exception as exc:
            self._handle_empty_exception(exc, context)
            return Empty()

    @log_service_call()
    async def GetRole(
        self,
        request: GetRoleRequest,
        context: ServicerContext,
    ) -> Role:
        try:
            self._require_user_id(request.user_id)
            role = await self._role_service.get_role(
                request.id,
                await self._context.create(request.user_id),
            )
            if role is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Role not found: {request.id}")
                return Role()
            return role.convert(self._to_grpc)
        except Exception as exc:
            return self._handle_role_exception(exc, context)

    @log_service_call()
    async def GetRoles(
        self,
        request: GetRolesRequest,
        context: ServicerContext,
    ) -> AsyncIterator[Role]:
        try:
            self._require_user_id(request.user_id)
            filter_entity = (
                to_role_filter_entity(request.filter)
                if request.HasField("filter")
                else RoleFilter()
            )
            roles = await self._role_service.get_roles(
                filter_entity,
                await self._context.create(request.user_id),
            )
            for role in roles:
                yield role.convert(self._to_grpc)
        except Exception as exc:
            self._handle_stream_exception(exc, context)
            return

    # ---- membership ------------------------------------------------------

    @log_service_call()
    async def AddUserToRole(
        self,
        request: AddUserToRoleRequest,
        context: ServicerContext,
    ) -> UserRoleMembership:
        try:
            self._require_user_id(request.user_id)
            self._require_id(request.subject_user_id, "subject_user_id")
            membership = await self._role_service.add_user_to_role(
                request.subject_user_id,
                request.role_id,
                await self._context.create(request.user_id),
            )
            return membership.convert(self._to_grpc)
        except Exception as exc:
            return self._handle_membership_exception(exc, context)

    @log_service_call()
    async def RemoveUserFromRole(
        self,
        request: RemoveUserFromRoleRequest,
        context: ServicerContext,
    ) -> Empty:
        try:
            self._require_user_id(request.user_id)
            self._require_id(request.subject_user_id, "subject_user_id")
            await self._role_service.remove_user_from_role(
                request.subject_user_id,
                request.role_id,
                await self._context.create(request.user_id),
            )
            return Empty()
        except Exception as exc:
            self._handle_empty_exception(exc, context)
            return Empty()

    @log_service_call()
    async def GetRolesForUser(
        self,
        request: GetRolesForUserRequest,
        context: ServicerContext,
    ) -> AsyncIterator[Role]:
        try:
            self._require_user_id(request.user_id)
            self._require_id(request.subject_user_id, "subject_user_id")
            roles = await self._role_service.get_roles_for_user(
                request.subject_user_id,
                await self._context.create(request.user_id),
            )
            for role in roles:
                yield role.convert(self._to_grpc)
        except Exception as exc:
            self._handle_stream_exception(exc, context)
            return

    @log_service_call()
    async def GetUsersForRole(
        self,
        request: GetUsersForRoleRequest,
        context: ServicerContext,
    ) -> AsyncIterator[UserRoleMembership]:
        try:
            self._require_user_id(request.user_id)
            self._require_id(request.role_id, "role_id")
            memberships = await self._role_service.get_users_for_role(
                request.role_id,
                await self._context.create(request.user_id),
            )
            for m in memberships:
                yield m.convert(self._to_grpc)
        except Exception as exc:
            self._handle_stream_exception(exc, context)
            return

    # ---- error handling --------------------------------------------------

    @staticmethod
    def _require_user_id(user_id: str) -> None:
        if not user_id:
            raise ValueError("user_id is required")

    @staticmethod
    def _require_id(value: str, name: str) -> None:
        if not value:
            raise ValueError(f"{name} is required")

    def _handle_role_exception(
        self,
        exc: Exception,
        context: ServicerContext,
    ) -> Role:
        self._set_context_error(exc, context)
        return Role()

    def _handle_membership_exception(
        self,
        exc: Exception,
        context: ServicerContext,
    ) -> UserRoleMembership:
        self._set_context_error(exc, context)
        return UserRoleMembership()

    def _handle_empty_exception(
        self,
        exc: Exception,
        context: ServicerContext,
    ) -> None:
        self._set_context_error(exc, context)

    def _handle_stream_exception(
        self,
        exc: Exception,
        context: ServicerContext,
    ) -> None:
        self._set_context_error(exc, context)

    def _set_context_error(self, exc: Exception, context: ServicerContext) -> None:
        if isinstance(exc, PermissionError):
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(exc))
            return

        if isinstance(exc, LookupError):
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return

        if isinstance(exc, ValueError):
            code = (
                grpc.StatusCode.NOT_FOUND
                if "not found" in str(exc).lower()
                else grpc.StatusCode.INVALID_ARGUMENT
            )
            context.set_code(code)
            context.set_details(str(exc))
            return

        self.log.error(f"Error handling role request: {traceback.format_exc()}")
        context.set_code(grpc.StatusCode.INTERNAL)
        context.set_details("Internal server error while handling role request")