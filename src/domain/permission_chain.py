from typing import *
from abc import ABC, abstractmethod

import urllib

from src import utils
from src.api.other.relationship import *
from src.api import Relationship, UserContextABC, PermissionRepoABC

# i am sorry, I don't want to inject it into each chain element. This is more for debugging
log = utils.logging_provider(__file__)


class CheckResult:
    """Convenience class which is more or less a bool with a reason for failure when the check fails."""
    def __init__(self, success: bool, error: Optional[Exception] = None):
        self.success = success
        self.error = error

    def __bool__(self):
        return self.success

class PermissionCheckChain(ABC):
    """Chain of responsibility for checking permissions."""
    _next: Optional["PermissionCheckChain"]
    _prev: Optional["PermissionCheckChain"]

    """repo which handles permission requests"""
    _repo: Optional[PermissionRepoABC]

    def __init__(self):
        self._next = None
        self._prev = None
        self._permission_repo = None

    @abstractmethod
    async def _check(self, user_ctx: UserContextABC) -> bool:
        """Actual implementation of the check"""
        ...

    async def check(self, user_ctx: UserContextABC) -> CheckResult:
        """Check if the permission applies or not. If it applies automatically call the next
        
        Returns
        --------
        CheckResult:
            `CheckResult.success` is `True` if the check was successful, and `False` if not. 
            `CheckResult.error` is the error to raise when the check fails, and `None` when it succeeds.

        Note
        ----
        CheckResult can be used like a boolean
        """

        if not self._permission_repo:
            raise RuntimeError("`PermissionCheckChain` was called in the wrong order." +
            "First call on the first element `.set_permission_repo()`, then in subsequent calls it will be passed automatically with `.set_next()`")
        success = await self._check(user_ctx)
        if not success:
            return CheckResult(False, self.error)
        if not self._next:
            return CheckResult(True, None)
        return await self._next.check(user_ctx)

    def set_permission_repo(self, repo: PermissionRepoABC) -> Self:
        self._permission_repo = repo
        return self
    
    def _get_permission_repo(self) -> PermissionRepoABC:
        if not self._permission_repo:
            raise RuntimeError("`PermissionCheckChain` was called in the wrong order." +
            "First call on the first element `.set_permission_repo()`, then in subsequent calls it will be passed automatically with `.set_next()`")
        return self._permission_repo

    def set_next(self, next: "PermissionCheckChain") -> "PermissionCheckChain":
        """Set the next chain element which is executed after this one"""
        self._next = next
        self._next.set_permission_repo(self._permission_repo)
        return next

    def get_first(self) -> "PermissionCheckChain":
        """Get the first element of the chain, used as starting point"""
        if not self._prev:
            return self
        return self._prev.get_first()
    
    @abstractmethod
    def _get_error_message(self) -> str:
        """Get the error message to raise when permission check fails, which gets inserted into the error generated in .get_error()"""
        ...

    @property
    def error(self) -> PermissionError:
        """Convenience method to get the error when permission check fails"""
        return PermissionError(self._get_error_message())
    

    def _get_relation(
        self,
        obj_id: str, 
        subj_id: str, 
        obj_type: ObjectRef | None = None, 
        relation_type: RelationName | None = None, 
        subj_type: SubjectRef | None = None
    ) -> Relationship:
        """
        Builds the relation to access SpiceDB by accessing a defined
        `self.OBJECT_TYPE`, `self.RELATION_TYPE` and `self.SUBJECT_TYPE`.

        Raises
        ------
        TypeError:
            if one of the three fields is missing 
        """
        try:
            obj_type = obj_type or self.OBJECT_TYPE  # type:ignore  
            subj_type = subj_type or self.SUBJECT_TYPE  # type:ignore
            relation_type = relation_type or self.RELATION_TYPE  #type:ignore
        except AttributeError:
            raise AttributeError("`PermissionCheckChain._get_relation()` is only callable when `OBJECT_TYPE`," + 
            "`SUBJECT_TYPE` and `RELATION_TYPE` is defined in the subclass")
        
        return Relationship(
            resource=ObjectRef(obj_type, obj_id),  # type:ignore
            relation=relation_type,
            subject=SubjectRef(subj_type, subj_id)  # type:ignore
        )
    
class PermissionCheckChainStart(PermissionCheckChain):
    """Starting point of the chain, which is a helper which does no perm checks at all 
    used to start when building a longer chain with a for loop"""
    def __init__(self, permission_repo: PermissionRepoABC):
        super().__init__()
        self._permission_repo = permission_repo

    async def _check(self, user_ctx: UserContextABC) -> bool:
        """This is the starting point of the chain, so it always returns True"""
        return True

    def _get_error_message(self) -> str:
        """This is the starting point of the chain, so it never raises an error"""
        return "PermissionCheckChainStart should never be called directly. Use `.set_next()` to add a chain element."

    

class HasNoteViewPerm(PermissionCheckChain):
    """Permission check for viewing a note."""
    OBJECT_TYPE: ObjectType = "note"
    RELATION_TYPE: NoteRelationName = NoteRelationEnum.VIEW
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, note_id: str):
        super().__init__()
        self._note_id = note_id


    async def _check(self, user_ctx: UserContextABC) -> bool:
        relationship = self._get_relation(self._note_id, user_ctx.user_id)
        try:
            return await self._permission_repo.check(relationship)
        except Exception as e:
            log.error(f"Error while checking permission for relationship {relationship}: {e}")
            raise e
    
    def _get_error_message(self) -> str:
        return f"user has no permission to view note {self._note_id}"

class HasAttachmentWritePerm(PermissionCheckChain):
    """Checks if a user can write to an attachment, which is required for uploading an attachment or linking it to a note."""
    OBJECT_TYPE: ObjectType = "attachment"
    RELATION_TYPE: NoteRelationName = AttachmentRelationEnum.WRITE
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, attachment_id: str) -> None:
        super().__init__()
        self._attachment_id = attachment_id
    
    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx, 
            permission=self.RELATION_TYPE, 
            resource=ObjectRef(self.OBJECT_TYPE, self._attachment_id)
        )
    
    def _get_error_message(self) -> str:
        return f"user has no permission to write to attachment {self._attachment_id} (e.g. delete it)"

class HasAttachmentViewPerm(PermissionCheckChain):
    """Checks is a user can view an attachment"""
    OBJECT_TYPE: ObjectType = "attachment"
    RELATION_TYPE: NoteRelationName = AttachmentRelationEnum.VIEW
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, attachment_id: str) -> None:
        super().__init__()
        self._attachment_id = attachment_id
    
    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx, 
            permission=self.RELATION_TYPE, 
            resource=ObjectRef(self.OBJECT_TYPE, self._attachment_id)
        )
    
    def _get_error_message(self) -> str:
        return f"user has no permission to view attachment {self._attachment_id}"

class HasNoteDeletePerm(PermissionCheckChain):
    """Permission check for deleting a note."""
    OBJECT_TYPE: ObjectType = "note"
    RELATION_TYPE: NoteRelationName = NoteRelationEnum.DELETE
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, note_id: str):
        super().__init__()
        self._note_id = note_id


    async def _check(self, user_ctx: UserContextABC) -> bool:
        # `delete` is a computed permission in SpiceDB, so check the
        # effective permission instead of expecting a direct relationship tuple.
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._note_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to delete note {self._note_id}"

class HasNoteWritePerm(PermissionCheckChain):
    """Permission check for writing/editing to a note."""
    OBJECT_TYPE: ObjectType = "note"
    RELATION_TYPE: NoteRelationName = NoteRelationEnum.WRITE
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, note_id: str):
        super().__init__()
        self._note_id = note_id


    async def _check(self, user_ctx: UserContextABC) -> bool:
        # `write` is a computed permission in SpiceDB, so check the
        # effective permission instead of expecting a direct relationship tuple.
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._note_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to write to note {self._note_id}"


class HasNoteEditPermissionsPerm(PermissionCheckChain):
    """Permission check for managing a note's sharing and permission settings."""
    OBJECT_TYPE: ObjectType = "note"
    RELATION_TYPE: NoteRelationName = NoteRelationEnum.EDIT_PERMISSIONS
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, note_id: str):
        super().__init__()
        self._note_id = note_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        # `edit_permissions` is a computed permission in SpiceDB, so check the
        # effective permission instead of expecting a direct relationship tuple.
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._note_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to edit permissions for note {self._note_id}"


class HasNoteManagePerm(PermissionCheckChain):
    """Permission check for attaching/detaching roles on a note.

    ``note#manage`` is granted to direct note admins and to admins of
    the parent directory.  See ``schema.zed`` for the cascade rule.
    """
    OBJECT_TYPE: ObjectType = "note"
    RELATION_TYPE: NoteRelationName = NoteRelationEnum.MANAGE
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, note_id: str) -> None:
        super().__init__()
        self._note_id = note_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        # `manage` is a computed permission in SpiceDB; check the
        # effective permission rather than expecting a direct tuple.
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._note_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no 'manage' permission on note {self._note_id}"


class HasRoleManagePerm(PermissionCheckChain):
    """Permission check for changing a role's membership.

    ``role#manage`` is granted to direct role administrators.  Note
    admins and directory admins do *not* inherit it -- attaching a
    role to a resource is a separate capability from changing who
    belongs to the role.
    """
    OBJECT_TYPE: ObjectType = "role"
    RELATION_TYPE: RoleRelationName = RoleRelationEnum.MANAGE
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, role_id: str) -> None:
        super().__init__()
        self._role_id = role_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._role_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no 'manage' permission on role {self._role_id}"


class HasDirectoryViewPerm(PermissionCheckChain):
    """Permission check for viewing a directory."""
    OBJECT_TYPE: ObjectType = "directory"
    RELATION_TYPE: DirectoryRelationName = DirectoryRelationEnum.VIEW
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, directory_id: str) -> None:
        super().__init__()
        self._directory_id = directory_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._directory_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to view directory {self._directory_id}"


class HasDirectoryWritePerm(PermissionCheckChain):
    """Permission check for writing/patching a directory."""
    OBJECT_TYPE: ObjectType = "directory"
    RELATION_TYPE: DirectoryRelationName = DirectoryRelationEnum.WRITE
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, directory_id: str) -> None:
        super().__init__()
        self._directory_id = directory_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._directory_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to write to directory {self._directory_id}"


class HasDirectoryDeletePerm(PermissionCheckChain):
    """Permission check for deleting a directory."""
    OBJECT_TYPE: ObjectType = "directory"
    RELATION_TYPE: DirectoryRelationName = DirectoryRelationEnum.DELETE
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, directory_id: str) -> None:
        super().__init__()
        self._directory_id = directory_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._directory_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to delete directory {self._directory_id}"


class HasDirectoryEditPermissionsPerm(PermissionCheckChain):
    """Permission check for managing a directory's sharing and permission settings."""
    OBJECT_TYPE: ObjectType = "directory"
    RELATION_TYPE: DirectoryRelationName = DirectoryRelationEnum.EDIT_PERMISSIONS
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, directory_id: str) -> None:
        super().__init__()
        self._directory_id = directory_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._directory_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to edit permissions for directory {self._directory_id}"


class HasShelfEditPermissionsPerm(PermissionCheckChain):
    """Permission check for managing a shelf's sharing and permission settings.

    Shelves use the same role set as directories, so the gate
    is identical -- only the resource type differs.
    """
    OBJECT_TYPE: ObjectType = "shelf"
    RELATION_TYPE: DirectoryRelationName = DirectoryRelationEnum.EDIT_PERMISSIONS
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, shelf_id: str) -> None:
        super().__init__()
        self._shelf_id = shelf_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._shelf_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to edit permissions for shelf {self._shelf_id}"


class HasShelfViewPerm(PermissionCheckChain):
    """Permission check for viewing a shelf and its book bindings."""
    OBJECT_TYPE: ObjectType = "shelf"
    RELATION_TYPE: DirectoryRelationName = DirectoryRelationEnum.VIEW
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, shelf_id: str) -> None:
        super().__init__()
        self._shelf_id = shelf_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._shelf_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to view shelf {self._shelf_id}"


class HasShelfWritePerm(PermissionCheckChain):
    """Permission check for mutating a shelf's row or its book bindings."""
    OBJECT_TYPE: ObjectType = "shelf"
    RELATION_TYPE: DirectoryRelationName = DirectoryRelationEnum.WRITE
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, shelf_id: str) -> None:
        super().__init__()
        self._shelf_id = shelf_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._shelf_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to write to shelf {self._shelf_id}"


class HasShelfDeletePerm(PermissionCheckChain):
    """Permission check for deleting a shelf."""
    OBJECT_TYPE: ObjectType = "shelf"
    RELATION_TYPE: DirectoryRelationName = DirectoryRelationEnum.DELETE
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, shelf_id: str) -> None:
        super().__init__()
        self._shelf_id = shelf_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._get_permission_repo().has_permission(
            user_ctx,
            permission=self.RELATION_TYPE,
            resource=ObjectRef(self.OBJECT_TYPE, self._shelf_id),
        )

    def _get_error_message(self) -> str:
        return f"user has no permission to delete shelf {self._shelf_id}"


class HasAnyDirectoryEditPermissionPerms(PermissionCheckChain):
    """Permission check: user has ``directory#edit_permissions`` on at least one directory.

    Used by the rules subsystem to gate creation of *global* rules
    (rules with no attached entity).  The user has to be able to
    manage at least one directory, otherwise they are effectively
    a pure viewer and the rules subsystem should not let them
    create rules that affect every event of a kind.

    Args:
        directory_facade: the directory facade used to enumerate
            the user's viewable directories. 
        max_candidates: cap on the number of directories to
            probe; defaults to 25. 
    """
    OBJECT_TYPE: ObjectType = "directory"
    RELATION_TYPE: DirectoryRelationName = DirectoryRelationEnum.EDIT_PERMISSIONS
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(
        self,
        directory_facade: Any,  # DirectoryFacadeABC; kept loose to avoid a cycle
        max_candidates: int = 25,
    ) -> None:
        super().__init__()
        self._directory_facade = directory_facade
        self._max_candidates = max_candidates

    async def _check(self, user_ctx: UserContextABC) -> bool:
        directory_ids = await self._directory_facade.list_user_directory_ids(
            user_ctx,
        )
        for directory_id in directory_ids[: self._max_candidates]:
            probe = (
                HasDirectoryEditPermissionsPerm(str(directory_id))
                .set_permission_repo(self._get_permission_repo())
            )
            ok = await probe._check(user_ctx)  # noqa: SLF001 -- intentional delegation
            if ok:
                return True
        return False

    def _get_error_message(self) -> str:
        return (
            "user has no 'edit_permissions' on any directory they can view"
        )


class HasAnyNoteManagePerm(PermissionCheckChain):
    """Permission check: user has ``note#manage`` on at least one note.

    Counterpart to :class:`HasAnyDirectoryEditPermissionsPerm` for
    the note side.  Probes the user's viewable notes and checks 
    ``note#manage`` on each.  Bounded by ``max_candidates`` (default 25)
    """
    OBJECT_TYPE: ObjectType = "note"
    RELATION_TYPE: NoteRelationName = NoteRelationEnum.MANAGE
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(
        self,
        directory_facade: Any,  # DirectoryFacadeABC; loose typing
        max_candidates: int = 25,
    ) -> None:
        super().__init__()
        self._directory_facade = directory_facade
        self._max_candidates = max_candidates

    async def _check(self, user_ctx: UserContextABC) -> bool:
        # Enumerate the user's viewable directories, then pull
        # the direct child notes under them, and probe ``manage``
        # on each.  This is intentionally less precise than a
        # dedicated ``list_user_note_ids`` would be, but the
        # project does not have that on the directory facade
        # yet and adding it would balloon the surface area of
        # this change.  The candidate cap still bounds the
        # worst case.
        directory_ids = await self._directory_facade.list_user_directory_ids(
            user_ctx,
        )
        candidate_note_ids: list[str] = []
        for directory_id in directory_ids:
            children = await self._directory_facade.get_children_of(
                "note", directory_id, depth=1,
            )
            candidate_note_ids.extend(children)
            if len(candidate_note_ids) >= self._max_candidates:
                break
        for note_id in candidate_note_ids[: self._max_candidates]:
            probe = (
                HasNoteManagePerm(str(note_id))
                .set_permission_repo(self._get_permission_repo())
            )
            ok = await probe._check(user_ctx)  # noqa: SLF001
            if ok:
                return True
        return False

    def _get_error_message(self) -> str:
        return "user has no 'manage' on any note they can view"


