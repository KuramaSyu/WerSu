"""Domain types for the SpiceDB-backed permission model.

This module defines the resource/relation/subject vocabulary used to
describe a single relationship, plus the ABC that adapters implement
to translate between these domain objects and a concrete backend
(SpiceDB, an in-memory store, ...).
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Literal, Optional, TypeAlias

from src.api.other.undefined import UNDEFINED, UndefinedOr


class ObjectTypeEnum(StrEnum):
    """SpiceDB object/resource kinds handled by the project."""

    NOTE = "note"
    DIRECTORY = "directory"
    USER = "user"
    ATTACHMENT = "attachment"
    ROLE = "role"
    SHELF = "shelf"
    PAGE = "page"


class NoteRelationEnum(StrEnum):
    """SpiceDB relations and permissions for note objects."""

    # relations
    ADMIN = "admin"
    WRITER = "writer"
    READER = "reader"
    PARENT_DIRECTORY = "parent_directory"
    OWNER = "owner"

    # grant / delegate capabilities (relations, not permissions)
    GRANT_READ = "grant_read"
    GRANT_WRITE = "grant_write"
    GRANT_ADMIN = "grant_admin"
    DELEGATE_READ = "delegate_read"
    DELEGATE_WRITE = "delegate_write"
    DELEGATE_ADMIN = "delegate_admin"

    # permissions
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"


class AttachmentRelationEnum(StrEnum):
    """SpiceDB relations and permissions for attachment objects."""

    # relations
    PARENT_NOTE = "parent_note"
    PARENT_USER = "parent_user"

    # permissions
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"


class DirectoryRelationEnum(StrEnum):
    """SpiceDB relations and permissions for directory objects."""

    # relations
    PARENT = "parent"
    OWNER = "owner"
    ADMIN = "admin"
    WRITER = "writer"
    READER = "reader"

    # grant / delegate capabilities (relations, not permissions)
    GRANT_READ = "grant_read"
    GRANT_WRITE = "grant_write"
    GRANT_ADMIN = "grant_admin"
    DELEGATE_READ = "delegate_read"
    DELEGATE_WRITE = "delegate_write"
    DELEGATE_ADMIN = "delegate_admin"

    # permissions
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"


class ShelfRelationEnum(StrEnum):
    """SpiceDB relations and permissions for shelf objects.

    Mirrors :class:`DirectoryRelationEnum` -- shelves share the
    same role set (owner / admin / writer / reader) because
    the rules subsystem treats shelf-attached rules as
    equivalent to directory-attached rules for permission
    gating.
    """

    # relations
    ADMIN = "admin"
    WRITER = "writer"
    READER = "reader"
    OWNER = "owner"

    # grant / delegate capabilities (relations, not permissions)
    GRANT_READ = "grant_read"
    GRANT_WRITE = "grant_write"
    GRANT_ADMIN = "grant_admin"
    DELEGATE_READ = "delegate_read"
    DELEGATE_WRITE = "delegate_write"
    DELEGATE_ADMIN = "delegate_admin"

    # permissions
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"


class UserRelationEnum(StrEnum):
    """SpiceDB relations on user objects (currently membership edges only)."""

    MEMBER_OF = "member_of"


class RoleRelationEnum(StrEnum):
    """SpiceDB relations and permissions on role objects."""

    # relations
    ADMIN = "admin"
    MEMBER = "member"
    GRANT_MEMBER = "grant_member"
    DELEGATE_MEMBER = "delegate_member"

    # Deprecated alias; the relation itself is now simply ``admin``.
    ADMINISTRATOR = "admin"


class PageRelationEnum(StrEnum):
    """SpiceDB relations and permissions on the global page object.

    Page administrators are site-wide administrators.  The ``admin``
    relation is intentionally mappable to ``user`` and ``role#member``
    subjects so the permission can be held directly or through any role
    (in particular the seeded ``Page Administrator`` role).
    """

    # relations
    ADMIN = "admin"
    GRANT_PAGE_ADMIN = "grant_page_admin"
    DELEGATE_PAGE_ADMIN = "delegate_page_admin"

    # Deprecated alias; the relation itself is now simply ``admin``.
    PAGE_ADMIN = "admin"

    # permissions
    CAN_GRANT_PAGE_ADMIN = "can_grant_page_admin"
    CAN_DELEGATE_PAGE_ADMIN = "can_delegate_page_admin"
    CAN_ASSIGN_DELEGATE_PAGE_ADMIN = "can_assign_delegate_page_admin"


#: The single SpiceDB ``page`` object carries site-wide permissions.
PAGE_ROOT_ID = "global"


ObjectType: TypeAlias = Literal[
    "note", "directory", "user", "attachment", "role", "shelf", "page"
]
"""String-literal union of every :class:`ObjectTypeEnum` value."""

SubjectType: TypeAlias = Literal["user", "directory", "role"]
"""String-literal union of every valid subject type."""

NoteRelationName: TypeAlias = Literal[
    "admin",
    "writer",
    "reader",
    "grant_read",
    "grant_write",
    "grant_admin",
    "delegate_read",
    "delegate_write",
    "delegate_admin",
    "view",
    "write",
    "delete",
    "parent_directory",
    "owner",
]
"""String-literal union of every :class:`NoteRelationEnum` value."""

DirectoryRelationName: TypeAlias = Literal[
    "parent",
    "owner",
    "admin",
    "writer",
    "reader",
    "grant_read",
    "grant_write",
    "grant_admin",
    "delegate_read",
    "delegate_write",
    "delegate_admin",
    "view",
    "write",
    "delete",
]

ShelfRelationName: TypeAlias = Literal[
    "owner",
    "admin",
    "writer",
    "reader",
    "grant_read",
    "grant_write",
    "grant_admin",
    "delegate_read",
    "delegate_write",
    "delegate_admin",
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

UserRelationName: TypeAlias = Literal["member_of"]
"""String-literal union of every :class:`UserRelationEnum` value."""

RoleRelationName: TypeAlias = Literal["admin", "member", "grant_member", "delegate_member"]
"""String-literal union of every :class:`RoleRelationEnum` value."""

PageRelationName: TypeAlias = Literal[
    "admin",
    "grant_page_admin",
    "delegate_page_admin",
    "can_grant_page_admin",
    "can_delegate_page_admin",
    "can_assign_delegate_page_admin",
]
"""String-literal union of every :class:`PageRelationEnum` value."""

RelationName: TypeAlias = (
    NoteRelationName
    | DirectoryRelationName
    | ShelfRelationName
    | AttachmentRelationName
    | UserRelationName
    | RoleRelationName
    | PageRelationName
)
"""Union of every relation-name literal across all resource types."""

RelationEnum: TypeAlias = (
    NoteRelationEnum
    | DirectoryRelationEnum
    | ShelfRelationEnum
    | AttachmentRelationEnum
    | UserRelationEnum
    | RoleRelationEnum
    | PageRelationEnum
)
"""Union of every relation :class:`enum.StrEnum` across all resource types."""


class ObjectRef:
    """Domain object representing a part of a Resource Tuple. Reference to a SpiceDB object, identified by type and id."""

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
        return (
            str(self.object_type) == str(other.object_type)
            and self.object_id == other.object_id
        )

    def __contains__(self, other: object) -> bool:
        """``value in self``: ``self`` is the pattern, ``other`` is the value.

        :obj:`~src.api.undefined.UNDEFINED` on ``self`` acts as a
        wildcard for that field.  Type fields (``object_type``) are not
        wildcards: a pattern with ``object_type="user"`` only matches
        values whose ``object_type`` is also ``"user"``.

        Example:
            ``ObjectRef("note", "1") in ObjectRef("note", UNDEFINED)``
            is True -- the UNDEFINED id on the pattern side is a wildcard.
        """
        if not isinstance(other, ObjectRef):
            return NotImplemented
        # ``object_type`` is structural -- never a wildcard.
        if str(self.object_type) != str(other.object_type):
            return False
        # ``self`` is the pattern, ``other`` is the value.
        return _pattern_matches(self.object_id, other.object_id)

    def __hash__(self) -> int:
        return hash((str(self.object_type), self.object_id))


class SubjectRef(ObjectRef):
    """Reference to a SpiceDB subject (the right-hand side of a relationship).

    Supports both bare-object subjects (``user:alice``) and userset
    subjects (``role:eng#member``).  The userset form is how
    ``note#reader: user | role#member`` style relations are written:
    the subject is a role, qualified by the relation that resolves
    "members of this role".

    ``optional_relation`` is therefore the role's ``member`` relation
    when we mean "every member of this role", e.g.::

        SubjectRef(
            ObjectTypeEnum.ROLE,
            role_id,
            optional_relation=RoleRelationEnum.MEMBER,
        )  # role:<role_id>#member

    For a bare user subject (``user:alice``) it stays ``None``.

    Args:
        object_type: subject's SpiceDB object type, e.g. ``"user"`` or
            ``"role"``.
        object_id: subject's id.  :obj:`~src.api.undefined.UNDEFINED` is
            legal for wildcard lookups but never for inserts.
        optional_relation: optional relation that turns this
            subject into a userset reference (``role:eng#member``
            when set to ``"member"``).  Defaults to ``None`` which
            means a plain object subject.
    """

    def __init__(
        self,
        object_type: SubjectType | ObjectTypeEnum,
        object_id: UndefinedOr[str],
        optional_relation: Optional[RelationName | RelationEnum] = None,
    ) -> None:
        super().__init__(object_type=object_type, object_id=object_id)
        self.optional_relation = optional_relation

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SubjectRef):
            return NotImplemented
        return (
            super().__eq__(other)
            and str(self.optional_relation or "") == str(other.optional_relation or "")
        )

    def __hash__(self) -> int:
        return hash((str(self.object_type), self.object_id, str(self.optional_relation or "")))


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

    def __contains__(self, other: object) -> bool:
        """``value in self``: ``self`` is the pattern, ``other`` is the value."""
        if not isinstance(other, PartialRelationship):
            return NotImplemented
        if not _pattern_matches(self.relation, other.relation):
            return False
        return self.subject in other.subject

    def __hash__(self) -> int:
        return hash((str(self.relation), self.subject))


class Relationship(PartialRelationship):
    """SpiceDB-style relationship between a resource, a relation, and a subject.

    The triple uses the standard SpiceDB notation:

    * general form: ``<object_type>:<object_id>#<relation>@<subject_type>:<subject_id>``
    * ``note:123#writer@user:alice`` -> Alice is a writer of note ``123``.
    * ``directory:456#parent@directory:789`` -> Directory ``456`` has parent directory ``789``.

    Equality is strict: two :class:`Relationship` instances are equal
    if their resource, relation and subject all match.  To express
    "match any value on some field", use the ``in`` operator -- a
    :class:`Relationship` with :obj:`~src.api.undefined.UNDEFINED` on
    one or more fields acts as a *pattern* and ``value in pattern``
    returns True when the value's concrete fields line up with the
    pattern's non-UNDEFINED fields.  Type fields (``object_type``) are
    never wildcards.

    Example:
        >>> value = Relationship(
        ...     resource=ObjectRef("note", "1"),
        ...     relation=NoteRelationEnum.READER,
        ...     subject=SubjectRef("user", "alice"),
        ... )
        >>> pattern = replace(value, relation=UNDEFINED)
        >>> value in pattern
        True
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

    def __contains__(self, other: object) -> bool:
        """``value in self``: ``self`` is the pattern, ``other`` is the value.

        The resource type and subject type are not wildcarded; only
        ``object_id`` and ``relation`` accept :obj:`UNDEFINED`.  The
        semantics propagate from :class:`ObjectRef` and
        :class:`PartialRelationship`.
        """
        if not isinstance(other, Relationship):
            return NotImplemented
        return (
            other.resource in self.resource
            and other.subject in self.subject
            and _pattern_matches(self.relation, other.relation)
        )

    def __hash__(self) -> int:
        return hash((self.resource, str(self.relation), self.subject))

    def __repr__(self):
        return f"Relationship(resource={self.resource.object_type}:{self.resource.object_id}, relation={self.relation}, subject={self.subject.object_type}:{self.subject.object_id})"


def _pattern_matches(pattern: object, value: object) -> bool:
    """The `UNDEFINED` pattern always contains value -> e.g. True. 
    Otherwise it checks equality. 
    """
    if pattern is UNDEFINED:
        return True
    return pattern == value


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