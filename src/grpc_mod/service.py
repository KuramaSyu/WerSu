from datetime import datetime, timezone
import traceback
from typing import AsyncIterator, List, Sequence

import grpc
from grpc.aio import ServicerContext
import asyncpg
from pprint import pformat
from google.protobuf.timestamp_pb2 import Timestamp
import time
import functools
import logging
import inspect

from src.api import LoggingProvider
from src.api.types import Pagination
from src.api.undefined import UNDEFINED
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities import NoteEntity
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.note import NoteRepoFacadeABC, UserContext
from src.db.repos.note.versioning import NoteVersionRepoABC
from src.db.repos.note.permission import NoteRelationEnum, ObjectRef, ObjectTypeEnum, RelationEnum, Relationship, SubjectRef
from src.db.repos.attachments.attachments import Attachment as AttachmentEntity
from src.db.entities.user.user import UserEntity
from src.grpc_mod.converter import (
    to_grpc_attachment,
    to_grpc_attachment_metadata,
    to_grpc_directory,
    to_grpc_note,
    to_grpc_user,
    to_object_ref,
    to_permission_object_type,
    to_permission_resource,
    to_relationship,
)
from src.grpc_mod.proto.attachments_pb2 import (
    Attachment as GrpcAttachment,
    AttachmentMetadata as GrpcAttachmentMetadata,
    DeleteAttachmentLinkRequest,
    DeleteAttachmentRequest,
    DeleteAttachmentResponse,
    GetAttachmentMetadataRequest,
    GetAttachmentRequest,
    PostAttachmentLinkRequest,
    PostAttachmentRequest,
)
from src.grpc_mod.proto.attachments_pb2_grpc import AttachmentServiceServicer
from src.grpc_mod.converter.note_entity_converter import to_grpc_minimal_note, to_search_type
from src.grpc_mod.proto.note_pb2 import (
    AlterNoteRequest,
    AlterDirectoryRequest,
    CreateDirectoryRequest,
    DeleteDirectoryRequest,
    CreatePermissionRequest,
    Directory,
    DeleteNoteRequest,
    DeletePermissionRequest,
    GetDirectoryRequest,
    GetDirectoriesRequest,
    GetNoteVersionContentRequest,
    GetNoteVersionsRequest,
    GetDirectoryActivityRequest,
    GetNoteRequest,
    GetPermissionsRequest,
    GetSearchNotesRequest,
    MinimalNote,
    Note,
    NoteVersionContent,
    NoteVersionSummary,
    PermissionRelationship,
    PermissionSubject,
    PermissionsResponse,
    PostNoteRequest,
    RestoreNoteVersionRequest,
    ReplacePermissionsRequest,
)
from src.grpc_mod.proto.note_pb2_grpc import (
    DirectoryServiceServicer,
    NoteServiceServicer,
    NoteVersionServiceServicer,
    PermissionServiceServicer,
)
from src.grpc_mod.proto.user_pb2 import (
    AlterUserRequest,
    DeleteUserRequest,
    DeleteUserResponse,
    GetUserRequest,
    PostUserRequest,
    User,
)
from src.grpc_mod.proto.user_pb2_grpc import UserServiceServicer
from src.services.attachments import AttachmentFacadeABC
from src.services.roles import PermissionServiceABC
from src.services.user import UserServiceABC
from src.services.versioning import DirectoryActivityServiceABC
from src.db import UserContext


# Decorator factory must be defined before use on service methods
def _log_service_call_factory(logger_name: str = "src.services", measure_time: bool = True):
    """Decorator factory for logging service method entry/exit and timing.

    Logs at INFO level for entry/exit summary, DEBUG level for detailed args/timing.
    Handles both async coroutine methods and async generator methods (streaming).
    The decorator will prefer a `self.log` logger on the instance if present;
    otherwise it will use `logging.getLogger(logger_name)`.
    """

    def decorator(func):
        is_generator = inspect.isasyncgenfunction(func)

        if is_generator:
            @functools.wraps(func)
            async def generator_wrapper(*args, **kwargs):
                self = args[0] if args else None
                logger = getattr(self, "log", None) or logging.getLogger(logger_name)
                class_name = self.__class__.__name__ if self else ""
                
                logger.info("Calling %s.%s", class_name, func.__name__)
                try:
                    logger.debug("  args=%s kwargs=%s", args[1:] if self else args, kwargs)
                except Exception:
                    pass

                start = time.perf_counter() if measure_time else None
                try:
                    async for item in func(*args, **kwargs):
                        yield item
                except Exception:
                    try:
                        logger.exception("Exception in %s.%s", class_name, func.__name__)
                    except Exception:
                        pass
                    raise
                finally:
                    if measure_time and start is not None:
                        elapsed = time.perf_counter() - start
                        logger.info("Completed %s.%s in %.3fs", class_name, func.__name__, elapsed)

            return generator_wrapper
        else:
            @functools.wraps(func)
            async def coroutine_wrapper(*args, **kwargs):
                self = args[0] if args else None
                logger = getattr(self, "log", None) or logging.getLogger(logger_name)
                class_name = self.__class__.__name__ if self else ""
                
                logger.info("Calling %s.%s", class_name, func.__name__)
                try:
                    logger.debug("  args=%s kwargs=%s", args[1:] if self else args, kwargs)
                except Exception:
                    pass

                start = time.perf_counter() if measure_time else None
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception:
                    try:
                        logger.exception("Exception in %s.%s", class_name, func.__name__)
                    except Exception:
                        pass
                    raise
                finally:
                    if measure_time and start is not None:
                        elapsed = time.perf_counter() - start
                        logger.info("Completed %s.%s in %.3fs", class_name, func.__name__, elapsed)

            return coroutine_wrapper

    return decorator


def log_service_call(logger_name: str = "src.services", measure_time: bool = True):
    """Convenience factory used as `@log_service_call()` or `@log_service_call("my.logger")`.

    Returns the actual decorator produced by `_log_service_call_factory`.
    """
    return _log_service_call_factory(logger_name=logger_name, measure_time=measure_time)


class GrpcNoteService(NoteServiceServicer):
    """
    Implements the gRPC service defined in grpc/proto/note.proto
    """

    def __init__(self, repo: NoteRepoFacadeABC, log: LoggingProvider):
        self.repo = repo
        self.log = log(__name__, self)
        self._svc_logger = logging.getLogger("src.services")
 
    @log_service_call()
    async def GetNote(self, request: GetNoteRequest, context: ServicerContext) -> Note:
        try:
            note_entity = await self.repo.select_by_id(request.id, UserContext(user_id=request.user_id))
            return to_grpc_note(note_entity)
        except Exception:
            self.log.error(f"Error fetching note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching note")
            return Note()

    @log_service_call()
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

    @log_service_call()
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

    @log_service_call()
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
        
    @log_service_call()
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


class GrpcDirectoryService(DirectoryServiceServicer):
    """gRPC adapter for directory read/write operations."""

    def __init__(self, directory_repo: DirectoryRepo, log: LoggingProvider):
        self._directory_repo = directory_repo
        self.log = log(__name__, self)

    @log_service_call()
    async def GetDirectory(self, request: GetDirectoryRequest, context: ServicerContext) -> Directory:  # pyright: ignore[reportIncompatibleMethodOverride]
        try:
            if not request.id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("id is required")
                return Directory()

            directory = await self._directory_repo.fetch_directory(request.id)
            if directory is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Directory not found")
                return Directory()

            return to_grpc_directory(directory)
        except Exception:
            self.log.error(f"Error fetching directory: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching directory")
            return Directory()

    @log_service_call()
    async def GetDirectories(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, request: GetDirectoriesRequest, context: ServicerContext
    ) -> AsyncIterator[Directory]:
        try:
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return

            if request.HasField("offset") and request.offset < 0:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("offset must be greater than or equal to 0")
                return
            if request.HasField("limit") and request.limit < 0:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("limit must be greater than or equal to 0")
                return

            directory_ids = await self._directory_repo.list_user_directory_ids(
                UserContext(user_id=request.user_id)
            )
            directories = []
            for directory_id in directory_ids:
                directory = await self._directory_repo.fetch_directory(directory_id)
                if directory is not None:
                    directories.append(directory)

            if request.HasField("parent_id"):
                directories = [
                    directory
                    for directory in directories
                    if directory.parent_id not in (UNDEFINED, None)
                    and str(directory.parent_id) == request.parent_id
                ]

            offset = request.offset if request.HasField("offset") else 0
            if request.HasField("limit"):
                directories = directories[offset: offset + request.limit]
            else:
                directories = directories[offset:]

            for directory in directories:
                yield to_grpc_directory(directory)
        except Exception:
            self.log.error(f"Error fetching directories: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching directories")
            return

    @log_service_call()
    async def CreateDirectory(self, request: CreateDirectoryRequest, context: ServicerContext) -> Directory:  # pyright: ignore[reportIncompatibleMethodOverride]
        try:
            if not request.name:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("name is required")
                return Directory()
            
            # directory#admin@user:<id> <-- this gets updated in SpiceDB Directory Repo
            user_admin_relation = Relationship(
                resource=ObjectRef(object_type=ObjectTypeEnum.DIRECTORY, object_id=UNDEFINED),
                relation=NoteRelationEnum.ADMIN,
                subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id=request.user_id),
            )

            directory = await self._directory_repo.create_directory(
                DirectoryEntity(
                    name=request.name,
                    display_name=request.display_name if request.HasField("display_name") else UNDEFINED,
                    description=request.description if request.HasField("description") else UNDEFINED,
                    image_url=request.image_url if request.HasField("image_url") else UNDEFINED,
                    parent_id=request.parent_id if request.HasField("parent_id") else UNDEFINED,
                    relations=[user_admin_relation],
                )
            )
            return to_grpc_directory(directory)
        except Exception:
            self.log.error(f"Error creating directory: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while creating directory")
            return Directory()

    @log_service_call()
    async def PatchDirectory(self, request: AlterDirectoryRequest, context: ServicerContext) -> Directory:  # pyright: ignore[reportIncompatibleMethodOverride]
        try:
            if not request.id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("id is required")
                return Directory()

            updated = await self._directory_repo.update_directory(
                DirectoryEntity(
                    id=request.id,
                    name=request.name if request.HasField("name") else UNDEFINED,
                    display_name=request.display_name if request.HasField("display_name") else UNDEFINED,
                    description=request.description if request.HasField("description") else UNDEFINED,
                    image_url=request.image_url if request.HasField("image_url") else UNDEFINED,
                    parent_id=request.parent_id if request.HasField("parent_id") else UNDEFINED,
                    relations=UNDEFINED,
                )
            )
            if updated is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Directory not found")
                return Directory()

            return to_grpc_directory(updated)
        except Exception:
            self.log.error(f"Error patching directory: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while patching directory")
            return Directory()

    @log_service_call()
    async def DeleteDirectory(self, request: DeleteDirectoryRequest, context: ServicerContext) -> Directory:  # pyright: ignore[reportIncompatibleMethodOverride]
        try:
            if not request.id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("id is required")
                return Directory()

            deleted = await self._directory_repo.delete_directory(DirectoryEntity(id=request.id))
            if not deleted:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Directory not found")
                return Directory()

            return Directory(id=request.id)
        except Exception:
            self.log.error(f"Error deleting directory: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while deleting directory")
            return Directory()


class GrpcNoteVersionService(NoteVersionServiceServicer):
    """gRPC adapter for note version history and restore operations."""

    def __init__(
        self,
        note_repo: NoteRepoFacadeABC,
        version_repo: NoteVersionRepoABC,
        directory_activity_service: DirectoryActivityServiceABC,
        log: LoggingProvider,
    ) -> None:
        self._note_repo = note_repo
        self._version_repo = version_repo
        self._directory_activity_service = directory_activity_service
        self.log = log(__name__, self)

    @log_service_call()
    async def GetNoteVersions(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, request: GetNoteVersionsRequest, context: ServicerContext
    ) -> AsyncIterator[NoteVersionSummary]:
        try:
            if not request.note_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("note_id is required")
                return

            limit = request.limit if request.HasField("limit") else 25
            offset = request.offset if request.HasField("offset") else 0
            entries = await self._version_repo.list_versions(request.note_id, limit, offset)
            for entry in entries:
                ts = Timestamp()
                ts.FromDatetime(entry.created_at)
                yield NoteVersionSummary(
                    version_id=entry.version_id,
                    note_id=entry.note_id,
                    version_index=entry.version_index,
                    created_at=ts,
                    author_id=entry.author_id,
                    is_snapshot=entry.is_snapshot,
                    snapshot_id=entry.snapshot_id or "",
                )
        except Exception:
            self.log.error(f"Error fetching note versions: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching note versions")
            return

    @log_service_call()
    async def GetNoteVersionContent(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, request: GetNoteVersionContentRequest, context: ServicerContext
    ) -> NoteVersionContent:
        try:
            if not request.note_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("note_id is required")
                return NoteVersionContent()

            version = await self._version_repo.get_content_at_version(
                note_id=request.note_id,
                version_index=request.version_index,
            )
            ts = Timestamp()
            ts.FromDatetime(version.created_at)
            return NoteVersionContent(
                note_id=version.note_id,
                version_index=version.version_index,
                created_at=ts,
                author_id=version.author_id,
                title=version.title,
                content=version.content,
            )
        except Exception:
            self.log.error(f"Error fetching note version content: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching note version content")
            return NoteVersionContent()

    @log_service_call()
    async def RestoreNoteVersion(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, request: RestoreNoteVersionRequest, context: ServicerContext
    ) -> Note:
        try:
            if not request.note_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("note_id is required")
                return Note()

            version = await self._version_repo.get_content_at_version(
                note_id=request.note_id,
                version_index=request.version_index,
            )
            # Apply the reconstructed content via the existing note update pipeline.
            updated = await self._note_repo.update(
                NoteEntity(
                    note_id=request.note_id,
                    author_id=version.author_id,
                    title=version.title,
                    content=version.content,
                    updated_at=datetime.now(),
                    embeddings=UNDEFINED,
                    permissions=UNDEFINED,
                ),
                UserContext(user_id=request.user_id),
            )
            return to_grpc_note(updated)
        except Exception:
            self.log.error(f"Error restoring note version: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while restoring note version")
            return Note()

    @log_service_call()
    async def GetDirectoryActivity(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, request: GetDirectoryActivityRequest, context: ServicerContext
    ) -> AsyncIterator[NoteVersionSummary]:
        try:
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return

            directory_id = (
                request.directory_id
                if request.HasField("directory_id") and request.directory_id
                else None
            )
            max_depth = request.max_depth if request.HasField("max_depth") else 10
            limit = request.limit if request.HasField("limit") else 25
            offset = request.offset if request.HasField("offset") else 0

            entries = await self._directory_activity_service.list_directory_activity(
                directory_id=directory_id,
                actor=UserContext(user_id=request.user_id),
                max_depth=max_depth,
                limit=limit,
                offset=offset,
            )

            for entry in entries:
                ts = Timestamp()
                ts.FromDatetime(entry.created_at)
                yield NoteVersionSummary(
                    version_id=entry.version_id,
                    note_id=entry.note_id,
                    version_index=entry.version_index,
                    created_at=ts,
                    author_id=entry.author_id,
                    is_snapshot=entry.is_snapshot,
                    snapshot_id=entry.snapshot_id or "",
                )
        except PermissionError as exc:
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(exc))
            return
        except Exception:
            self.log.error(f"Error fetching directory activity: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching directory activity")
            return


class GrpcPermissionService(PermissionServiceServicer):
    """gRPC adapter for permission relationship management.

    This service validates request payloads, delegates authorization/business
    checks to ``PermissionServiceABC``, and maps domain models to/from protobuf
    messages.
    """

    def __init__(self, permission_service: PermissionServiceABC, log: LoggingProvider):
        self._permission_service = permission_service
        self.log = log(__name__, self)

    @log_service_call()
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

    @log_service_call()
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

    @log_service_call()
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

    @log_service_call()
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

    @log_service_call()
    async def GetUser(self, request: GetUserRequest, context: ServicerContext) -> User:
        try:
            return await self._GetUser(request, context)
        except Exception:
            self.log.error(f"Error fetching user: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching user")
            return User()

    @log_service_call()
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

    @log_service_call()
    async def AlterUser(self, request: AlterUserRequest, context: ServicerContext) -> User:
        ...
    
    @log_service_call()
    async def DeleteUser(self, request: DeleteUserRequest, context: ServicerContext) -> DeleteUserResponse:
        ...
    
    @log_service_call()
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


class GrpcAttachmentService(AttachmentServiceServicer):
    """Implements the gRPC service defined in grpc/proto/attachments.proto."""

    def __init__(self, attachment_service: AttachmentFacadeABC, log: LoggingProvider):
        self.attachment_service = attachment_service
        self.log = log(__name__, self)

    @log_service_call()
    async def PostAttachment(
        self, request: PostAttachmentRequest, context: ServicerContext
    ) -> GrpcAttachment:
        try:
            now = datetime.now()
            attachment = AttachmentEntity(
                key=UNDEFINED,
                filename=request.filename,
                filepath=request.filepath,
                content_type=request.content_type or "application/octet-stream",
                size=len(request.content),
                created_at=now,
                updated_at=now,
                content=request.content,
            )
            
            created = await self.attachment_service.post_attachment(attachment, UserContext(request.user_id))
            return to_grpc_attachment(created)
        except ValueError as exc:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return GrpcAttachment()
        except Exception:
            self.log.error(f"Error creating attachment: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while creating attachment")
            return GrpcAttachment()

    @log_service_call()
    async def GetAttachment(
        self, request: GetAttachmentRequest, context: ServicerContext
    ) -> GrpcAttachment:
        try:
            attachment = await self.attachment_service.get_attachment(request.key, UserContext(request.user_id))
            return to_grpc_attachment(attachment)
        except KeyError as exc:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return GrpcAttachment()
        except Exception:
            self.log.error(f"Error fetching attachment: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching attachment")
            return GrpcAttachment()

    @log_service_call()
    async def GetAttachmentMetadata(
        self, request: GetAttachmentMetadataRequest, context: ServicerContext
    ) -> GrpcAttachmentMetadata:
        try:
            attachment = await self.attachment_service.get_metadata(request.key, UserContext(request.user_id))
            return to_grpc_attachment_metadata(attachment)
        except KeyError as exc:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return GrpcAttachmentMetadata()
        except Exception:
            self.log.error(f"Error fetching attachment metadata: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching attachment metadata")
            return GrpcAttachmentMetadata()

    @log_service_call()
    async def DeleteAttachment(
        self, request: DeleteAttachmentRequest, context: ServicerContext
    ) -> DeleteAttachmentResponse:
        try:
            await self.attachment_service.delete_attachment(request.key, UserContext(request.user_id))
            return DeleteAttachmentResponse(success=True)
        except Exception:
            self.log.error(f"Error deleting attachment: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while deleting attachment")
            return DeleteAttachmentResponse(success=False)
        
    @log_service_call()
    async def PostAttachmentLink(self, request: PostAttachmentLinkRequest, context: ServicerContext) -> None:
        try:
            await self.attachment_service.link_attachment_to_note(
                attachment_key=request.attachment_key,
                note_id=request.note_id,
                user_ctx=UserContext(request.user_id),
            )
        except Exception:
            self.log.error(f"Error linking attachment: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while linking attachment")

    @log_service_call()
    async def DeleteAttachmentLink(self, request: DeleteAttachmentLinkRequest, context: ServicerContext) -> None:
        try:
            await self.attachment_service.unlink_attachment_from_note(
                attachment_key=request.attachment_key,
                note_id=request.note_id,
                user_ctx=UserContext(request.user_id),
            )
        except Exception:
            self.log.error(f"Error linking attachment: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while unlinking attachment")        