"""gRPC adapter for :class:`src.api.DirectoryServiceABC`.

Implements the directory endpoints from ``grpc/proto/note.proto``
(``DirectoryServiceImpl``): CRUD plus the paginated
``GetNotesOfDirectory`` stream.  Translates proto payloads into
:class:`~src.db.entities.directory.directory.DirectoryEntity`
arguments, delegates to the service layer, and maps results back
through the injected :class:`ConvertToGrpcVisitor`.
"""

from __future__ import annotations

import traceback
from typing import AsyncIterator

import grpc
from grpc.aio import ServicerContext

from src.api import DirectoryServiceABC, LoggingProvider
from src.api.services.directory_service import DirectoryIncludeOptions
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import ContextFactory, UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.note_pb2 import (
    AlterDirectoryRequest,
    CreateDirectoryRequest,
    DeleteDirectoryRequest,
    Directory,
    GetDirectoriesRequest,
    GetDirectoryRequest,
    GetNotesOfDirectoryRequest,
    NotesReply,
)
from src.grpc_mod.proto.note_pb2_grpc import DirectoryServiceServicer


class GrpcDirectoryService(DirectoryServiceServicer):
    """gRPC adapter for directory read/write operations."""

    def __init__(
        self,
        directory_service: DirectoryServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ):
        self._directory_service = directory_service
        self.log = log(__name__, self)
        self._to_grpc = to_grpc
        self._context = context_factory

    @log_service_call()
    async def GetDirectory(self, request: GetDirectoryRequest, context: ServicerContext[GetDirectoryRequest, Directory]) -> Directory:  # pyright: ignore[reportIncompatibleMethodOverride]
        try:
            if not request.id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("id is required")
                return Directory()
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return Directory()

            user_ctx = await self._context.create(request.user_id)
            include: DirectoryIncludeOptions = {
                "include_parents": request.include_parents,
                "include_child_dirs": request.include_child_dirs,
                "include_child_notes": request.include_child_notes,
            }
            directory = await self._directory_service.get_directory(
                request.id, user_ctx, include=include,
            )
            if directory is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Directory not found")
                return Directory()

            return directory.convert(self._to_grpc)
        except PermissionError as e:
            self.log.warning(f"Permission denied in GetDirectory: {e}")
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(e))
            return Directory()
        except Exception:
            self.log.error(f"Error fetching directory: {traceback.format_exc()}\nwith request: {request}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching directory")
            return Directory()

    @log_service_call()
    async def GetDirectories(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, request: GetDirectoriesRequest, context: ServicerContext[GetDirectoriesRequest, Directory]
    ) -> AsyncIterator[Directory]:
        try:
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return

            user_ctx = await self._context.create(request.user_id)
            parent_id = (
                request.parent_id if request.HasField("parent_id") else None
            )
            limit = request.limit if request.HasField("limit") else None
            offset = request.offset if request.HasField("offset") else None

            # No aggregate counts in this fetch -- direct child
            # counts are derived by the client from
            # `len(child_directory_ids)` and
            # `len(child_note_ids)` once those lists are populated.
            include: DirectoryIncludeOptions = {
                "include_child_dirs": request.include_child_dirs,
                "include_child_notes": request.include_child_notes,
                "include_parents": request.include_parents
            }
            directories = await self._directory_service.get_directories(
                user_ctx=user_ctx,
                parent_id=parent_id,
                limit=limit,
                offset=offset,
                include=include,
            )

            for directory in directories:
                yield directory.convert(self._to_grpc)
        except PermissionError as e:
            self.log.warning(f"Permission denied in GetDirectories: {e}")
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(e))
            return
        except ValueError as e:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(e))
            return
        except Exception:
            self.log.error(f"Error fetching directories: {traceback.format_exc()}\nwith request: {request}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching directories")
            return

    @log_service_call()
    async def CreateDirectory(self, request: CreateDirectoryRequest, context: ServicerContext[CreateDirectoryRequest, Directory]) -> Directory:  # pyright: ignore[reportIncompatibleMethodOverride]
        try:
            if not request.name:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("name is required")
                return Directory()
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return Directory()

            user_ctx = await self._context.create(request.user_id)
            parent_ids = list(request.parent_ids)
            directory = await self._directory_service.create_directory(
                DirectoryEntity(
                    id=UNDEFINED,
                    slug=request.name,
                    display_name=request.display_name if request.HasField("display_name") else UNDEFINED,
                    description=request.description if request.HasField("description") else UNDEFINED,
                    image_url=request.image_url if request.HasField("image_url") else UNDEFINED,
                    parent_directory_ids=parent_ids if parent_ids else UNDEFINED,
                    relations=[],
                ),
                user_ctx,
            )
            return directory.convert(self._to_grpc)
        except PermissionError as e:
            self.log.warning(f"Permission denied in CreateDirectory: {e}")
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(e))
            return Directory()
        except Exception:
            self.log.error(f"Error creating directory: {traceback.format_exc()}\nwith request: {request}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while creating directory")
            return Directory()

    @log_service_call()
    async def PatchDirectory(self, request: AlterDirectoryRequest, context: ServicerContext[AlterDirectoryRequest, Directory]) -> Directory:  # pyright: ignore[reportIncompatibleMethodOverride]
        try:
            if not request.id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("id is required")
                return Directory()
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return Directory()

            user_ctx = await self._context.create(request.user_id)
            parent_ids_provided = len(request.parent_ids) > 0
            updated = await self._directory_service.patch_directory(
                DirectoryEntity(
                    id=request.id,
                    slug=request.name if request.HasField("name") else UNDEFINED,
                    display_name=request.display_name if request.HasField("display_name") else UNDEFINED,
                    description=request.description if request.HasField("description") else UNDEFINED,
                    image_url=request.image_url if request.HasField("image_url") else UNDEFINED,
                    parent_directory_ids=(
                        list(request.parent_ids)
                        if parent_ids_provided
                        else UNDEFINED
                    ),
                    tag_ids=UNDEFINED,
                    relations=UNDEFINED,
                ),
                user_ctx,
            )
            if updated is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Directory not found")
                return Directory()

            return updated.convert(self._to_grpc)
        except PermissionError as e:
            self.log.warning(f"Permission denied in PatchDirectory: {e}")
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(e))
            return Directory()
        except Exception:
            self.log.error(f"Error patching directory: {traceback.format_exc()}\nwith request: {request}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while patching directory")
            return Directory()

    @log_service_call()
    async def DeleteDirectory(self, request: DeleteDirectoryRequest, context: ServicerContext[DeleteDirectoryRequest, Directory]) -> Directory:  # pyright: ignore[reportIncompatibleMethodOverride]
        try:
            if not request.id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("id is required")
                return Directory()
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return Directory()

            user_ctx = await self._context.create(request.user_id)
            deleted = await self._directory_service.delete_directory(
                request.id, user_ctx
            )
            if not deleted:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Directory not found")
                return Directory()

            return Directory(id=request.id)
        except PermissionError as e:
            self.log.warning(f"Permission denied in DeleteDirectory: {e}")
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(e))
            return Directory()
        except Exception:
            self.log.error(f"Error deleting directory: {traceback.format_exc()}\nwith request: {request}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while deleting directory")
            return Directory()

    @log_service_call()
    async def GetNotesOfDirectory(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        request: GetNotesOfDirectoryRequest,
        context: ServicerContext[GetNotesOfDirectoryRequest, NotesReply],
    ):
        """Return a :class:`NotesReply` with the paginated notes of ``request.directory_id``.

        The first page (offset 0) always contains a ``README.md``
        note; when one is missing from the directory the service
        creates it before yielding results.  Later pages return
        ordinary notes ordered after the README.
        """
        try:
            if not request.directory_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("directory_id is required")
                return NotesReply()
            if not request.user_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("user_id is required")
                return NotesReply()
            if request.limit < 0:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("limit must be greater than or equal to 0")
                return NotesReply()
            if request.offset < 0:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("offset must be greater than or equal to 0")
                return NotesReply()

            user_ctx = await self._context.create(request.user_id)
            notes = await self._directory_service.get_directory_notes(
                directory_id=request.directory_id,
                user_ctx=user_ctx,
                limit=request.limit,
                offset=request.offset,
            )
            return self._to_grpc.visit_notes_reply(notes)
        except PermissionError as e:
            self.log.warning(f"Permission denied in GetNotesOfDirectory: {e}")
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(e))
            return NotesReply()
        except ValueError as e:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(e))
            return NotesReply()
        except Exception:
            self.log.error(f"Error fetching notes of directory: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching directory notes")
            return NotesReply()