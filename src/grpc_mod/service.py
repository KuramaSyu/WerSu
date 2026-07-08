"""Backward-compatible re-exports for the per-service gRPC adapters.

The implementations were split out of this module into dedicated
files (one per ``*Service``).  New code should import from those
files directly; this shim only exists so that ``from
src.grpc_mod.service import GrpcXService`` keeps working for older
call sites and tests.
"""

from __future__ import annotations

from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.attachment_service import GrpcAttachmentService
from src.grpc_mod.directory_service import GrpcDirectoryService
from src.grpc_mod.note_service import GrpcNoteService
from src.grpc_mod.note_version_service import GrpcNoteVersionService
from src.grpc_mod.permission_service import GrpcPermissionService
from src.grpc_mod.user_service import GrpcUserService

__all__ = [
    "GrpcAttachmentService",
    "GrpcDirectoryService",
    "GrpcNoteService",
    "GrpcNoteVersionService",
    "GrpcPermissionService",
    "GrpcUserService",
    "log_service_call",
]