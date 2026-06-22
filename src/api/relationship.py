from abc import ABC, abstractmethod
from enum import StrEnum
from operator import ge
from typing import Any, List, Literal, Optional, Protocol, TypeAlias, cast

from authzed.api.v1 import (

    Relationship as SpicedbRelationship,

)
from grpcutil import insecure_bearer_token_credentials
from src.api import UNDEFINED, UndefinedNoneOr, UndefinedOr

class ObjectTypeEnum(StrEnum):
    """Represents SpiceDB objects/resources"""
    NOTE = "note"
    DIRECTORY = "directory"
    USER = "user"
    ATTACHMENT = "attachment"


class NoteRelationEnum(StrEnum):
    """Represents SpiceDB relations/permissions for note objects"""
    ADMIN = "admin"
    WRITER = "writer"
    READER = "reader"
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"
    EDIT_PERMISSIONS = "edit_permissions"
    PARENT_DIRECTORY = "parent_directory"
    OWNER = "owner"


class AttachmentRelationEnum(StrEnum):
    """Represents permissions for attachments"""
    # relations
    PARENT_NOTE = "parent_note"
    
    # permissions
    WRITE = "write"
    VIEW = "view"
    DELETE = "delete"


class DirectoryRelationEnum(StrEnum):
    """Represents SpiceDB relations/permissions for directory objects"""
    PARENT = "parent"
    ADMIN = "admin"
    WRITER = "writer"
    READER = "reader"
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"


ObjectType: TypeAlias = Literal["note", "directory", "user", "attachment"]
SubjectType: TypeAlias = Literal["user", "directory"]
NoteRelationName: TypeAlias = Literal[
    "admin",
    "writer",
    "reader",
    "view",
    "write",
    "delete",
    "edit_permissions",
    "parent_directory",
    "owner",
]
DirectoryRelationName: TypeAlias = Literal[
    "parent",
    "admin",
    "writer",
    "reader",
    "view",
    "write",
    "delete",
]
AttachmentRelationName: TypeAlias = Literal[
    "view",
    "write",
    "delete"
]
RelationName: TypeAlias = NoteRelationName | DirectoryRelationName | AttachmentRelationName
RelationEnum: TypeAlias = NoteRelationEnum | DirectoryRelationEnum | AttachmentRelationEnum

class ObjectRef:
    def __init__(
        self,
        object_type: ObjectType | ObjectTypeEnum,
        object_id: UndefinedOr[str],
    ) -> None:
        self.object_type = object_type
        self.object_id = object_id
      

class SubjectRef(ObjectRef):
    def __init__(
        self,
        object_type: SubjectType | ObjectTypeEnum,
        object_id: UndefinedOr[str],
    ) -> None:
        super().__init__(object_type=object_type, object_id=object_id)

class PartialRelationship:
    def __init__(
        self,
        relation: UndefinedOr[RelationName | RelationEnum],
        subject: SubjectRef,
    ) -> None:
        self.relation = relation
        self.subject = subject

class Relationship(PartialRelationship):
    """
    Representa a relationship which is used to store permissions and relations between notes, users and directories. 
    The notation is like the following:
    - general form: <object_type>:<object_id>#<relation>@<subject_type>:<subject_id>
    - example: note:123#writer@user:alice -> Alice is a writer of note with id 123
    - example: directory:456#parent@directory:789 -> Directory with id 456 has parent directory with id 789
    """
    def __init__(
        self,
        resource: ObjectRef,
        relation: UndefinedOr[RelationName | RelationEnum],
        subject: SubjectRef,
    ) -> None:
        self.resource = resource
        super().__init__(relation, subject)

    def __repr__(self):
        return f"Relationship(resource={self.resource.object_type}:{self.resource.object_id}, relation={self.relation}, subject={self.subject.object_type}:{self.subject.object_id})"

class PermissionConverterABC(ABC):

    @abstractmethod
    def convert_object_ref(self, object_ref: ObjectRef) -> Any:
        ...

    @abstractmethod
    def convert_subject_ref(self, subject_ref: SubjectRef) -> Any:
        ...

    @abstractmethod
    def convert_relationship(self, relationship: Relationship) -> Any:
        ...
