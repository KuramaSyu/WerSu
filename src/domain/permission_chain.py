from typing import *
from abc import ABC, abstractmethod

import urllib

from src import utils
from src.api.relationship import *
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
    _next: Optional[PermissionCheckChain]
    _prev: Optional[PermissionCheckChain]
    
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

    def set_next(self, next: PermissionCheckChain) -> PermissionCheckChain:
        """Set the next chain element which is executed after this one"""
        self._next = next
        self._next.set_permission_repo(self._permission_repo)
        return next

    def get_first(self) -> PermissionCheckChain:
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
        return await self._permission_repo.has_permission(
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
        return await self._permission_repo.has_permission(
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
        relationship = self._get_relation(self._note_id, user_ctx.user_id)
        return await self._permission_repo.check(relationship)
    
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
        relationship = self._get_relation(self._note_id, user_ctx.user_id)
        return await self._permission_repo.check(relationship)
    
    def _get_error_message(self) -> str:
        return f"user has no permission to write to note {self._note_id}"


