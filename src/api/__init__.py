"""Public api-layer contracts.

Re-exports the abstract base classes, sentinels and small value
types that the service and grpc layers depend on.  Concrete
implementations live under :mod:`src.db` and :mod:`src.services`.
"""

from .types import LoggingProvider
from .undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from .service_unavailable_error import ServiceUnavailableError
from .user_context import UserContextABC
from .permission_repo import *
from .relationship import *
from .user_action import UserActionRepoABC
from .activity import ActivityRepoABC, ActivityFilterBuilder
from .activity_logger_service import (
    ActivityLoggerError,
    ActivityLoggerServiceABC,
    RoleChangeMetadata,
    RoleGrantMetadata,
    RoleRevokeMetadata,
)
from .activity_statistics_service import (
    ActivityStatisticsServiceABC,
    Algorithm,
)
from .note_service import (
    GetNotesOptions,
    GetNotesOptionsBuilder,
    NoteIncludeOptions,
    NoteResponse,
    NoteServiceABC,
    resolve_include_options,
)
from .note_facade import NoteRepoFacadeABC, SearchType
from .combined_note_repo import CombinedNoteRepoABC
from .note_tag_repo import NoteTagRepoABC
from .directory_service import DirectoryServiceABC
from .directory_service import (
    DirectoryIncludeOptions,
    resolve_directory_include_options,
)
from .directory_repo import DefaultDirectorySpec, DirectoryFacade
from .visitor import AcceptsVisitor, EntityVisitor