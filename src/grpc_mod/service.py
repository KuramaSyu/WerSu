from datetime import datetime
import traceback
from typing import AsyncIterator, List, Sequence

import grpc
from grpc.aio import ServicerContext
import asyncpg
from pprint import pformat

from src.api import LoggingProvider
from src.api.types import Pagination
from src.api.undefined import UNDEFINED
from src.db.entities import NoteEntity
from src.db.repos.note.note import NoteRepoFacadeABC, UserContext
from src.db.repos.note.permission import ObjectRef, ObjectTypeEnum, Relationship
from src.db.entities.user.user import UserEntity
from src.grpc_mod.converter import (
    to_grpc_note,
    to_grpc_user,
    to_object_ref,
    to_permission_object_type,
    to_permission_resource,
    to_relationship,
)
from src.grpc_mod.converter.note_entity_converter import to_grpc_minimal_note, to_search_type
from src.grpc_mod.proto.note_pb2 import (
    AlterNoteRequest,
    CreatePermissionRequest,
    DeleteNoteRequest,
    DeletePermissionRequest,
    GetNoteRequest,
    GetPermissionsRequest,
    GetSearchNotesRequest,
    MinimalNote,
    Note,
    PermissionRelationship,
    PermissionSubject,
    PermissionsResponse,
    PostNoteRequest,
    ReplacePermissionsRequest,
)
from src.grpc_mod.proto.note_pb2_grpc import NoteServiceServicer, PermissionServiceServicer
from src.grpc_mod.proto.user_pb2 import (
    AlterUserRequest,
    DeleteUserRequest,
    DeleteUserResponse,
    GetUserRequest,
    PostUserRequest,
    User,
)
from src.grpc_mod.proto.user_pb2_grpc import UserServiceServicer
from src.services.roles import PermissionServiceABC
from src.services.user import UserServiceABC


class GrpcNoteService(NoteServiceServicer):
    """
    Implements the gRPC service defined in grpc/proto/note.proto
    """

    def __init__(self, repo: NoteRepoFacadeABC, log: LoggingProvider):
        self.repo = repo
        self.log = log(__name__, self)
 
    async def GetNote(self, request: GetNoteRequest, context: ServicerContext) -> Note:
        try:
            note_entity = await self.repo.select_by_id(request.id, UserContext(user_id=request.user_id))
            return to_grpc_note(note_entity)
        except Exception:
            self.log.error(f"Error fetching note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching note")
            return Note()

    async def PostNote(self, request: PostNoteRequest, context: ServicerContext) -> Note:
        try:
            user_context = UserContext(request.author_id)
            note_entity = await self.repo.insert(
                NoteEntity(
                    note_id=UNDEFINED,
                    author_id=request.author_id,
                    content=request.content,
                    embeddings=[],
                    permissions=UNDEFINED,
                    title=request.title,
                    updated_at=datetime.now(),
                ),
                user=user_context,
            )
            return to_grpc_note(note_entity)
        except asyncpg.UniqueViolationError as e:
            context.set_code(grpc.StatusCode.ALREADY_EXISTS)
            context.set_details(f"Insertion error: {e}")
            return Note()
        except Exception:
            self.log.error(f"Error creating note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while creating note")
            return Note()

    async def PatchNote(self, request: AlterNoteRequest, context: ServicerContext) -> Note:
        try:
            note_entity = await self.repo.update(
                NoteEntity(
                    note_id=request.id,
                    author_id=request.author_id,
                    content=request.content,
                    embeddings=UNDEFINED,
                    permissions=UNDEFINED,
                    title=request.title,
                    updated_at=datetime.now(),
                ),
                UserContext(user_id=request.author_id)
            )
            self.log.debug(f"Updated note entity: {note_entity}")
            return to_grpc_note(note_entity)
        except Exception:
            self.log.error(f"Error updating note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while updating note")
            return Note()

    async def DeleteNote(self, request: DeleteNoteRequest, context: ServicerContext) -> Note:
        try:
            deleted_note_entities = await self.repo.delete(
                request.id,
                UserContext(user_id=request.author_id)
            )
            
            if deleted_note_entities is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Note not found where user with id {request.author_id} has permissions")
                return Note()
            assert len(deleted_note_entities) <= 1
            return to_grpc_note(deleted_note_entities[0])
        except Exception:
            self.log.error(f"Error deleting note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while deleting note")
            return Note()
        
    async def SearchNotes(
        self, request: GetSearchNotesRequest, context: ServicerContext
    ) -> AsyncIterator[MinimalNote]:
        notes = await self.repo.search_notes(
            to_search_type(request.search_type),
            request.query,
            pagination=Pagination(limit=request.limit, offset=request.offset),
            ctx=UserContext(user_id=request.user_id),
        )
        for note in notes:
            grpc_note = to_grpc_minimal_note(note)
            self.log.debug(f"[SearchNotes] yielding note: {pformat(grpc_note)}")
            yield grpc_note


class GrpcPermissionService(PermissionServiceServicer):
    """gRPC adapter for permission relationship management.

    This service validates request payloads, delegates authorization/business
    checks to ``PermissionServiceABC``, and maps domain models to/from protobuf
    messages.
    """

    def __init__(self, permission_service: PermissionServiceABC, log: LoggingProvider):
        self._permission_service = permission_service
        self.log = log(__name__, self)

    async def GetPermissions(self, request: GetPermissionsRequest, context: ServicerContext) -> PermissionsResponse:
        try:
            resource = to_object_ref(request.object_type, request.object_id)
            relationships = await self._permission_service.list_relationships(
                resource=resource,
                actor=UserContext(request.user_id),
            )
            return self._to_permissions_response(resource, relationships)
        except Exception as exc:
            return self._handle_permissions_exception(exc, context)

    async def CreatePermission(self, request: CreatePermissionRequest, context: ServicerContext) -> PermissionsResponse:
        try:
            resource = to_object_ref(request.object_type, request.object_id)
            relationship = to_relationship(resource, request.relationship)
            relationships = await self._permission_service.create_relationship(
                relationship=relationship,
                actor=UserContext(request.user_id),
            )
            return self._to_permissions_response(resource, relationships)
        except Exception as exc:
            return self._handle_permissions_exception(exc, context)

    async def DeletePermission(self, request: DeletePermissionRequest, context: ServicerContext) -> PermissionsResponse:
        try:
            resource = to_object_ref(request.object_type, request.object_id)
            relationship = to_relationship(resource, request.relationship)
            relationships = await self._permission_service.delete_relationship(
                relationship=relationship,
                actor=UserContext(request.user_id),
            )
            return self._to_permissions_response(resource, relationships)
        except Exception as exc:
            return self._handle_permissions_exception(exc, context)

    async def ReplacePermissions(self, request: ReplacePermissionsRequest, context: ServicerContext) -> PermissionsResponse:
        try:
            resource = to_object_ref(request.object_type, request.object_id)
            relationships = [
                to_relationship(resource, rel)
                for rel in request.relationships
            ]
            updated = await self._permission_service.replace_relationships(
                resource=resource,
                relationships=relationships,
                actor=UserContext(request.user_id),
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


class GrpcUserService(UserServiceServicer):
    """
    Implements the gRPC service defined in grpc/proto/user.proto
    """

    def __init__(self, user_service: UserServiceABC, log: LoggingProvider):
        self.user_service = user_service
        self.log = log(__name__, self)

    async def GetUser(self, request: GetUserRequest, context: ServicerContext) -> User:
        try:
            return await self._GetUser(request, context)
        except Exception:
            self.log.error(f"Error fetching user: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching user")
            return User()

    async def _GetUser(self, request: GetUserRequest, context: ServicerContext) -> User:
        if request.HasField("id"):
            user_entity = await self.user_service.get_user(user_id=request.id)
        elif request.HasField("discord_id"):
            user_entity = await self.user_service.get_user(discord_id=request.discord_id)
        else:
            # Neither id nor discord_id provided
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Either 'id' or 'discord_id' must be provided")
            return User()

        if user_entity is None:
            # User not found
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("User not found")
            return User()
        
        # user found and converted to gRPC User Message
        return to_grpc_user(user_entity)

    async def AlterUser(self, request: AlterUserRequest, context: ServicerContext) -> User:
        ...
    
    async def DeleteUser(self, request: DeleteUserRequest, context: ServicerContext) -> DeleteUserResponse:
        ...
    
    async def PostUser(self, request: PostUserRequest, context: ServicerContext) -> User:
        try:
            user_entity = await self.user_service.create_user(
                UserEntity(
                    id=None,
                    discord_id=request.discord_id,
                    avatar=request.avatar,
                    username=request.username,
                    discriminator=request.discriminator,
                    email=request.email,
                )
            )
            self.log.debug(f"Created user entity: {user_entity}")
            return to_grpc_user(user_entity)
        except asyncpg.UniqueViolationError:
            context.set_code(grpc.StatusCode.ALREADY_EXISTS)
            context.set_details("User with the given discord_id already exists")
            return User()
        except Exception:
            self.log.error(f"Error creating user: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while creating user")
            return User()
