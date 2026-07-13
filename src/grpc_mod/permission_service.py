"""gRPC adapter for :class:`src.services.PermissionServiceABC`.

Implements ``PermissionService`` from ``grpc/proto/note.proto``:
list / create / delete / replace relationships on a resource.
The proto <-> domain translation lives in
:mod:`src.grpc_mod.converter.permission_relationship_converter`;
this module is responsible for context construction, exception
mapping, and assembling the response shape.
"""

from __future__ import annotations

import traceback
from typing import Sequence

import grpc
from grpc.aio import ServicerContext

from src.api import LoggingProvider, ObjectRef, ObjectTypeEnum, Relationship
from src.api.other.user_context import ContextFactory, UserContextABC
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.converter.permission_relationship_converter import (
    to_object_ref,
    to_permission_object_type,
    to_permission_resource,
    to_relationship,
)
from src.grpc_mod.proto.note_pb2 import (
    CreatePermissionRequest,
    DeletePermissionRequest,
    GetPermissionsRequest,
    PermissionRelationship,
    PermissionSubject,
    PermissionsResponse,
    ReplacePermissionsRequest,
)
from src.grpc_mod.proto.note_pb2_grpc import PermissionServiceServicer
from src.services import PermissionServiceABC


class GrpcPermissionService(PermissionServiceServicer):
    """gRPC adapter for permission relationship management.

    This service validates request payloads, delegates authorization/business
    checks to ``PermissionServiceABC``, and maps domain models to/from protobuf
    messages.
    """

    def __init__(
        self,
        permission_service: PermissionServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ):
        self._permission_service = permission_service
        self.log = log(__name__, self)
        self._to_grpc = to_grpc
        self._context = context_factory

    @log_service_call()
    async def GetPermissions(self, request: GetPermissionsRequest, context: ServicerContext) -> PermissionsResponse:
        try:
            resource = to_object_ref(request.object_type, request.object_id)
            actor = await self._context.create(request.user_id)
            relationships = await self._permission_service.list_relationships(
                resource=resource,
                actor=actor,
            )
            return self._to_permissions_response(resource, relationships)
        except Exception as exc:
            return self._handle_permissions_exception(exc, context)

    @log_service_call()
    async def CreatePermission(self, request: CreatePermissionRequest, context: ServicerContext) -> PermissionsResponse:
        try:
            resource = to_object_ref(request.object_type, request.object_id)
            relationship = to_relationship(resource, request.relationship)
            actor = await self._context.create(request.user_id)
            relationships = await self._permission_service.create_relationship(
                relationship=relationship,
                actor=actor,
            )
            return self._to_permissions_response(resource, relationships)
        except Exception as exc:
            return self._handle_permissions_exception(exc, context)

    @log_service_call()
    async def DeletePermission(self, request: DeletePermissionRequest, context: ServicerContext) -> PermissionsResponse:
        try:
            resource = to_object_ref(request.object_type, request.object_id)
            relationship = to_relationship(resource, request.relationship)
            actor = await self._context.create(request.user_id)
            relationships = await self._permission_service.delete_relationship(
                relationship=relationship,
                actor=actor,
            )
            return self._to_permissions_response(resource, relationships)
        except Exception as exc:
            return self._handle_permissions_exception(exc, context)

    @log_service_call()
    async def ReplacePermissions(self, request: ReplacePermissionsRequest, context: ServicerContext) -> PermissionsResponse:
        try:
            resource = to_object_ref(request.object_type, request.object_id)
            relationships = [
                to_relationship(resource, rel)
                for rel in request.relationships
            ]
            actor = await self._context.create(request.user_id)
            updated = await self._permission_service.replace_relationships(
                resource=resource,
                relationships=relationships,
                actor=actor,
            )
            return self._to_permissions_response(resource, updated)
        except Exception as exc:
            return self._handle_permissions_exception(exc, context)

    def _to_permissions_response(
        self,
        resource: ObjectRef,
        relationships: Sequence[Relationship],
    ) -> PermissionsResponse:
        object_type = to_permission_object_type(ObjectTypeEnum(str(resource.object_type)))

        return PermissionsResponse(
            object_type=object_type,
            object_id=str(resource.object_id),
            relationships=[
                PermissionRelationship(
                    relation=str(rel.relation),
                    subject=PermissionSubject(
                        object_type=to_permission_object_type(ObjectTypeEnum(str(rel.subject.object_type))),
                        object_id=str(rel.subject.object_id),
                    ),
                    resource=to_permission_resource(rel.resource),
                )
                for rel in relationships
            ],
        )

    def _handle_permissions_exception(self, exc: Exception, context: ServicerContext) -> PermissionsResponse:
        if isinstance(exc, PermissionError):
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(exc))
            return PermissionsResponse()

        if isinstance(exc, LookupError):
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return PermissionsResponse()

        if isinstance(exc, ValueError):
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return PermissionsResponse()

        self.log.error(f"Error handling permissions: {traceback.format_exc()}")
        context.set_code(grpc.StatusCode.INTERNAL)
        context.set_details("Internal server error while managing permissions")
        return PermissionsResponse()