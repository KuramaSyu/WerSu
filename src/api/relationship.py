"""Domain types for the SpiceDB-backed permission model.

This module defines the resource/relation/subject vocabulary used to
describe a single relationship, plus the ABC that adapters implement
to translate between these domain objects and a concrete backend
(SpiceDB, an in-memory store, ...).
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Literal, TypeAlias

from src.api import UNDEFINED, UndefinedOr


class ObjectTypeEnum(StrEnum):
    """SpiceDB object/resource kinds handled by the project."""

    NOTE = "note"
    DIRECTORY = "directory"
    USER = "user"
    ATTACHMENT = "attachment"


class NoteRelationEnum(StrEnum):
    """SpiceDB relations and permissions for note objects."""

    # relations
    ADMIN = "admin"
    WRITER = "writer"
    READER = "reader"
    PARENT_DIRECTORY = "parent_directory"
    OWNER = "owner"

    # permissions
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"
    EDIT_PERMISSIONS = "edit_permissions"


class AttachmentRelationEnum(StrEnum):
    """SpiceDB relations and permissions for attachment objects."""

    # relations
    PARENT_NOTE = "parent_note"

    # permissions
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"


class DirectoryRelationEnum(StrEnum):
    """SpiceDB relations and permissions for directory objects."""

    # relations
    PARENT = "parent"
    ADMIN = "admin"
    WRITER = "writer"
    READER = "reader"

    # permissions
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"


ObjectType: TypeAlias = Literal["note", "directory", "user", "attachment"]
"""String-literal union of every :class:`ObjectTypeEnum` value."""

SubjectType: TypeAlias = Literal["user", "directory"]
"""String-literal union of every valid subject type."""

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
"""String-literal union of every :class:`NoteRelationEnum` value."""

DirectoryRelationName: TypeAlias = Literal[
    "parent",
    "admin",
    "writer",
    "reader",
    "view",
    "write",
    "delete",
]
"""String-literal union of every :class:`DirectoryRelationEnum` value."""

AttachmentRelationName: TypeAlias = Literal[
    "view",
    "write",
    "delete",
]
"""String-literal union of every :class:`AttachmentRelationEnum` value."""

RelationName: TypeAlias = NoteRelationName | DirectoryRelationName | AttachmentRelationName
"""Union of every relation-name literal across all resource types."""

RelationEnum: TypeAlias = NoteRelationEnum | DirectoryRelationEnum | AttachmentRelationEnum
"""Union of every relation :class:`enum.StrEnum` across all resource types."""


class ObjectRef:
    """Reference to a SpiceDB object, identified by type and id."""

    def __init__(
        self,
        object_type: ObjectType | ObjectTypeEnum,
        object_id: UndefinedOr[str],
    ) -> None:
        self.object_type = object_type
        self.object_id = object_id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ObjectRef):
            return NotImplemented
        return str(self.object_type) == str(other.object_type) and self.object_id == other.object_id

    def __hash__(self) -> int:
        return hash((str(self.object_type), self.object_id))


class SubjectRef(ObjectRef):
    """Reference to a SpiceDB subject (the right-hand side of a relationship)."""

    def __init__(
        self,
        object_type: SubjectType | ObjectTypeEnum,
        object_id: UndefinedOr[str],
    ) -> None:
        super().__init__(object_type=object_type, object_id=object_id)


class PartialRelationship:
    """Relation + subject half of a :class:`Relationship`, without the resource."""

    def __init__(
        self,
        relation: UndefinedOr[RelationName | RelationEnum],
        subject: SubjectRef,
    ) -> None:
        self.relation = relation
        self.subject = subject

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PartialRelationship):
            return NotImplemented
        return str(self.relation) == str(other.relation) and self.subject == other.subject

    def __hash__(self) -> int:
        return hash((str(self.relation), self.subject))


class Relationship(PartialRelationship):
    """SpiceDB-style relationship between a resource, a relation, and a subject.

    The triple uses the standard SpiceDB notation:

    * general form: ``<object_type>:<object_id>#<relation>@<subject_type>:<subject_id>``
    * ``note:123#writer@user:alice`` -> Alice is a writer of note ``123``.
    * ``directory:456#parent@directory:789`` -> Directory ``456`` has parent directory ``789``.

    Any field may be :obj:`~src.api.undefined.UNDEFINED` to act as a wildcard
    when the relationship is used as a filter (e.g. for deletes or lookups).
    """

    def __init__(
        self,
        resource: ObjectRef,
        relation: UndefinedOr[RelationName | RelationEnum],
        subject: SubjectRef,
    ) -> None:
        self.resource = resource
        super().__init__(relation, subject)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Relationship):
            return NotImplemented
        return self.resource == other.resource and super().__eq__(other)

    def __hash__(self) -> int:
        return hash((self.resource, str(self.relation), self.subject))

    def __repr__(self):
        return f"Relationship(resource={self.resource.object_type}:{self.resource.object_id}, relation={self.relation}, subject={self.subject.object_type}:{self.subject.object_id})"


class PermissionConverterABC(ABC):
    """Adapter contract between domain :class:`Relationship` objects and a backend.

    Implementations translate the api-layer types into whatever shape the
    underlying permission backend expects (SpiceDB protobuf messages,
    SQL rows, ...).

    Implementations:
    * :class:`src.db.repos.permissions.permission.SpicedbPermissionConverter`
    """

    @abstractmethod
    def convert_object_ref(self, object_ref: ObjectRef) -> Any:
        """Convert ``object_ref`` into the backend-native representation.

        Args:
            object_ref: domain reference to translate.

        Returns:
            Any: backend-native value (e.g. a protobuf message).
        """
        ...

    @abstractmethod
    def convert_subject_ref(self, subject_ref: SubjectRef) -> Any:
        """Convert ``subject_ref`` into the backend-native representation.

        Args:
            subject_ref: domain subject reference to translate.

        Returns:
            Any: backend-native value (e.g. a protobuf message).
        """
        ...

    @abstractmethod
    def convert_relationship(self, relationship: Relationship) -> Any:
        """Convert ``relationship`` into the backend-native representation.

        Args:
            relationship: domain relationship to translate.

        Returns:
            Any: backend-native value (e.g. a protobuf message).
        """
        ...