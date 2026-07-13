"""gRPC adapter for note version history and directory activity.

Wraps ``NoteVersionService`` from ``grpc/proto/note.proto``:
listing note versions, fetching a specific version's content,
restoring an older version, and the directory-level activity
feed.  Reads go straight to the version/activity repos; the
restore path funnels through ``NoteRepoFacadeABC.update`` so the
permission checks and post-update hooks stay consistent with
regular note edits.
"""

from __future__ import annotations

import traceback
from datetime import datetime
from typing import AsyncIterator

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.facades.note_facade import NoteRepoFacadeABC
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import ContextFactory, UserContextABC
from src.db.entities import NoteEntity
from src.db.repos.note.versioning import NoteVersionRepoABC
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.note_pb2 import (
    GetDirectoryActivityRequest,
    GetNoteVersionContentRequest,
    GetNoteVersionsRequest,
    Note,
    NoteVersionContent,
    NoteVersionSummary,
    RestoreNoteVersionRequest,
)
from src.grpc_mod.proto.note_pb2_grpc import NoteVersionServiceServicer
from src.services.directory_activity_service import DirectoryActivityServiceABC


class GrpcNoteVersionService(NoteVersionServiceServicer):
    """gRPC adapter for note version history and restore operations."""

    def __init__(
        self,
        note_repo: NoteRepoFacadeABC,
        version_repo: NoteVersionRepoABC,
        directory_activity_service: DirectoryActivityServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._note_repo = note_repo
        self._version_repo = version_repo
        self._directory_activity_service = directory_activity_service
        self.log = log(__name__, self)
        self._to_grpc = to_grpc
        self._context = context_factory

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
            user_ctx = await self._context.create(request.user_id)
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
                user_ctx,
            )
            return updated.convert(self._to_grpc)
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

            actor = await self._context.create(request.user_id)
            entries = await self._directory_activity_service.list_directory_activity(
                directory_id=directory_id,
                actor=actor,
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