from typing import *
from abc import ABC, abstractmethod

from src.services import Relationship, UserContextABC


class PermissionCheckChain(ABC):
    """Chain of responsibility for checking permissions."""
    _next: Optional[PermissionCheckChain]
    _prev: Optional[PermissionCheckChain]
    
    """repo which handles permission requests"""
    _repo: Optional[PermissionRepoABC]

    @abstractmethod
    async def _check(self, user_ctx: UserContextABC) -> bool:
        """Actual implementation of the check"""
        ...

    async def check(self, user_ctx: UserContextABC) -> bool:
        """Check if the permission applies or not. If it applies automatically call the next"""
        if not self._permission_repo:
            raise RuntimeError("`PermissionCheckChain` was called in the wrong order." +
            "First call on the first element `.set_permission_repo()`, then in subsequent calls it will be passed automatically with `.set_next()`")
        success = await self._check(user_ctx)
        if not success:
            return False
        if not self._next:
            return True
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
        self._note_id = note_id

    async def _check(self, user_ctx: UserContextABC) -> bool:
        relationship = self._get_relation(self._note_id, user_ctx.user_id)
        return await self._permission_repo.check(relationship)


class HasAttachmentViewPerm(PermissionCheckChain):
    """Checks is a user can view an attachment"""
    OBJECT_TYPE: ObjectType = "attachment"
    RELATION_TYPE: NoteRelationName = AttachmentRelationEnum.VIEW
    SUBJECT_TYPE: SubjectType = "user"

    def __init__(self, attachment_id: str) -> None:
        self._attachment_id = attachment_id
    
    async def _check(self, user_ctx: UserContextABC) -> bool:
        return await self._permission_repo.has_permission(
            user_ctx, 
            permission=self.RELATION_TYPE, 
            resource=ObjectRef(self.OBJECT_TYPE, self._attachment_id)
        )


