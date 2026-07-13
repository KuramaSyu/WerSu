"""Public api-layer contracts.

The api layer is split into four subpackages:

* :mod:`src.api.repos`    - storage contracts (single-table ABCs)
* :mod:`src.api.facades`  - composite repo contracts (cross-table)
* :mod:`src.api.services` - application service contracts
* :mod:`src.api.other`    - shared types, sentinels, identity,
                             visitor and logging-provider protocols

This ``__init__`` re-exports every public name so the historical
``from src.api import X`` import paths keep working.  New code is
encouraged to import from the subpackage directly so the layer of a
given name is visible at the call site.

Concrete implementations live under :mod:`src.db.repos` and
:mod:`src.services`.
"""

# relationship vocabulary is imported first because every other
# subpackage eventually re-exports it; resolving it early keeps
# circular imports through `from src.api import ...` stable.
from src.api.other.relationship import (
    AttachmentRelationEnum,
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    PermissionConverterABC,
    RelationEnum,
    RelationName,
    Relationship,
    SubjectRef,
    SubjectType,
)

# shared / cross-layer types
from src.api.other.types import LoggingProvider, Pagination
from src.api.other.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
    UndefinedType,
)
from src.api.other.user_context import (
    ActorAs,
    ContextFactory,
    UserContextABC,
    UserTypeT,
)
from src.api.other.service_unavailable_error import ServiceUnavailableError
from src.api.other.visitor import AcceptsVisitor, EntityVisitor

# storage contracts (single-table repos)
from src.api.repos.permission_repo import (
    PermissionRepoABC,
    ResolvedChildren,
    DirectoryChild,
)
from src.api.repos.activity_repo import ActivityRepoABC, ActivityFilterBuilder
from src.api.repos.user_action_repo import UserActionRepoABC
from src.api.repos.combined_note_repo import CombinedNoteRepoABC
from src.api.repos.note_tag_repo import NoteTagRepoABC
from src.api.repos.directory_repo import DirectoryRepoABC

# composite repo contracts (facades)
from src.api.facades.directory_facade import (
    DefaultDirectorySpec,
    DirectoryFacadeABC,
)
from src.api.facades.note_facade import NoteRepoFacadeABC, SearchType

# application service contracts
from src.api.services.directory_service import (
    DirectoryServiceABC,
    DirectoryIncludeOptions,
    resolve_directory_include_options,
)
from src.api.services.note_service import (
    GetNotesOptions,
    GetNotesOptionsBuilder,
    NoteIncludeOptions,
    NoteResponse,
    NoteServiceABC,
    resolve_include_options,
)
from src.api.services.activity_logger_service import (
    ActivityLoggerError,
    ActivityLoggerServiceABC,
    RoleChangeMetadata,
    RoleGrantMetadata,
    RoleRevokeMetadata,
)
from src.api.services.activity_statistics_service import (
    ActivityStatisticsServiceABC,
    Algorithm,
)
from src.api.services.attachment_facade import AttachmentFacadeABC
from src.api.services.directory_activity_service import DirectoryActivityServiceABC
from src.api.services.sharing import (
    ShareAccessServiceABC,
    SharingRepoABC,
    SharingServiceABC,
)
from src.api.services.user_service import UserServiceABC
from src.api.services.jwt_provider import (
    AttachmentTokenClaims,
    JwtError,
    JwtProvider,
)


__all__ = [
    # relationship vocabulary (must come first; see top-of-file note)
    "AttachmentRelationEnum",
    "DirectoryRelationEnum",
    "NoteRelationEnum",
    "ObjectRef",
    "ObjectTypeEnum",
    "PermissionConverterABC",
    "RelationEnum",
    "RelationName",
    "Relationship",
    "SubjectRef",
    # shared
    "LoggingProvider",
    "Pagination",
    "UNDEFINED",
    "UndefinedNoneOr",
    "UndefinedOr",
    "UndefinedType",
    "ServiceUnavailableError",
    "ActorAs",
    "ContextFactory",
    "UserContextABC",
    "UserTypeT",
    "AcceptsVisitor",
    "EntityVisitor",
    # repos
    "PermissionRepoABC",
    "ResolvedChildren",
    "DirectoryChild",
    "ActivityRepoABC",
    "ActivityFilterBuilder",
    "UserActionRepoABC",
    "CombinedNoteRepoABC",
    "NoteTagRepoABC",
    "DirectoryRepoABC",
    # facades
    "DefaultDirectorySpec",
    "DirectoryFacadeABC",
    "NoteRepoFacadeABC",
    "SearchType",
    # services
    "DirectoryServiceABC",
    "DirectoryIncludeOptions",
    "resolve_directory_include_options",
    "GetNotesOptions",
    "GetNotesOptionsBuilder",
    "NoteIncludeOptions",
    "NoteResponse",
    "NoteServiceABC",
    "resolve_include_options",
    "ActivityLoggerError",
    "ActivityLoggerServiceABC",
    "RoleChangeMetadata",
    "RoleGrantMetadata",
    "RoleRevokeMetadata",
    "ActivityStatisticsServiceABC",
    "Algorithm",
    "AttachmentFacadeABC",
    "DirectoryActivityServiceABC",
    "ShareAccessServiceABC",
    "SharingRepoABC",
    "SharingServiceABC",
    "UserServiceABC",
    "AttachmentTokenClaims",
    "JwtError",
    "JwtProvider",
    # relationship vocabulary
    "AttachmentRelationEnum",
    "DirectoryRelationEnum",
    "NoteRelationEnum",
    "ObjectRef",
    "ObjectTypeEnum",
    "PermissionConverterABC",
    "RelationEnum",
    "RelationName",
    "Relationship",
    "SubjectRef",
    "SubjectType",
]