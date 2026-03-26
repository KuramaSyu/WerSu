from abc import ABC, abstractmethod
from copy import deepcopy
from enum import StrEnum
from operator import ge
import re
from typing import Any, List, Literal, Optional, Protocol, TypeAlias, cast

from asyncpg import Record
from authzed.api.v1.permission_service_pb2 import ExportBulkRelationshipsRequest, ImportBulkRelationshipsRequest
from src.api.user_context import UserContextABC
from src.db.table import TableABC
from src.utils import asdict

from authzed.api.v1 import (
    BulkExportRelationshipsRequest,
    BulkImportRelationshipsRequest,
    CheckPermissionRequest,
    CheckPermissionResponse,
    AsyncClient,
    Consistency,
    DeleteRelationshipsRequest,
    LookupResourcesRequest,
    ObjectReference,
    Relationship as SpicedbRelationship,
    RelationshipFilter,
    SubjectFilter,
    SubjectReference,
    WriteSchemaRequest,
)
from grpcutil import insecure_bearer_token_credentials

from src.api import UNDEFINED, UndefinedNoneOr, UndefinedOr


class ObjectTypeEnum(StrEnum):
    NOTE = "note"
    DIRECTORY = "directory"
    USER = "user"


class NoteRelationEnum(StrEnum):
    ADMIN = "admin"
    WRITER = "writer"
    READER = "reader"
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"
    PARENT_DIRECTORY = "parent_directory"
    OWNER = "owner"


class DirectoryRelationEnum(StrEnum):
    PARENT = "parent"
    ADMIN = "admin"
    WRITER = "writer"
    READER = "reader"
    VIEW = "view"
    WRITE = "write"
    DELETE = "delete"


ObjectType: TypeAlias = Literal["note", "directory", "user"]
SubjectType: TypeAlias = Literal["user", "directory"]
NoteRelationName: TypeAlias = Literal[
    "admin",
    "writer",
    "reader",
    "view",
    "write",
    "delete",
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
RelationName: TypeAlias = NoteRelationName | DirectoryRelationName
RelationEnum: TypeAlias = NoteRelationEnum | DirectoryRelationEnum

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
        relation: RelationName | RelationEnum,
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
        relation: RelationName | RelationEnum,
        subject: SubjectRef,
    ) -> None:
        self.resource = resource
        super().__init__(relation, subject)

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

class SpicedbPermissionConverter(PermissionConverterABC):
    def convert_object_ref(self, object_ref: ObjectRef) -> ObjectReference:
        assert object_ref.object_id != UNDEFINED, "object_id must be provided for object reference"
        return ObjectReference(
            object_type=object_ref.object_type,
            object_id=str(object_ref.object_id)
        )

    def convert_subject_ref(self, subject_ref: SubjectRef) -> SubjectReference:
        return SubjectReference(
            object=self.convert_object_ref(subject_ref)
        )
    
    def convert_relationship(self, relationship: Relationship) -> SpicedbRelationship:
        return SpicedbRelationship(
            resource=self.convert_object_ref(relationship.resource),
            relation=relationship.relation,
            subject=self.convert_subject_ref(relationship.subject)
        )

class NotePermissionRepo(ABC):

    @abstractmethod
    async def insert(
        self,
        relationships: List[Relationship]
    ) -> List[Relationship]:
        """inserts permission
        
        Args:
        -----
        relationships: `List[Relationship]`
            the relationships to insert. Provide full subject, permission and object.

        Returns:
        --------
        `List[Relationship]`:
            the inserted relationships
        """
        ...

    @abstractmethod
    async def delete(
        self,
        relationship: Relationship
    ) -> Relationship:
        """delete permission
        
        Args:
        -----
        relationships: `List[Relationship]`
            the relationships to delete. Provide full subject and permission, and for object leave the 
            object_id to `UNDEFINED` and only provide the object_type. This allows to delete all permissions 
            for a given subject and permission on all objects of a given type.

        Returns:
        --------
        `List[Relationship]`:
            the deleted relationships
        """
        ...

    @abstractmethod
    async def lookup(
        self,
        relationship: Relationship
    ) -> List[ObjectRef]:
        """select permission
        
        Args:
        -----
        relationship: `Relationship`
            the relationship to lookup. Provide full subject and permission, and for object leave the 
            object_id to `UNDEFINED` and only provide the object_type. This allows to lookup all permissions 
            for a given subject and permission on all objects of a given type.

        Returns:
        --------
        `List[ObjectRef]`:
            the matching objects
        """
        ...

    @abstractmethod
    async def lookup_relationships(
        self,
        relationship: Relationship,
    ) -> List[Relationship]:
        """Select stored direct relationships by a relationship-shaped filter.

        Args:
        -----
        relationship: `Relationship`
            filter where `UNDEFINED` values act as wildcards for ids.

        Returns:
        --------
        `List[Relationship]`:
            matching stored direct relationships.
        """
        ...

    @abstractmethod
    async def lookup_notes(
        self,
        user: UserContextABC,
        permission: str
    ) -> List[ObjectRef]:
        """Retrieves all notes where the given user has the given permission
        
        Args:
        -----
        user: `UserContextABC`
            the user context to lookup permissions for
        permission: `str`
            the permission to lookup

        Returns:
        --------
        `List[ObjectRef]`:
            the matching objects
        """
        ...

    @abstractmethod
    async def list_relationships(self, resource: ObjectRef) -> List[Relationship]:
        """List stored relationships for a specific resource.

        Parameters
        ----------
        resource : ObjectRef
            Resource whose direct relationships should be returned.
            If `object_id` is `UNDEFINED`, all relationships for that
            resource type are returned.

        Returns
        -------
        List[Relationship]
            Stored relationships for `resource` including relation and subject.
        """
        ...

    @abstractmethod
    async def has_permission(
        self,
        user: UserContextABC,
        permission: str,
        resource: ObjectRef,
    ) -> bool:
        """Check whether a user has a permission on a resource.

        Parameters
        ----------
        user : UserContextABC
            Current user context.
        permission : str
            Permission to verify.
        resource : ObjectRef
            Resource to check against.

        Returns
        -------
        bool
            True if permission is granted.
        """
        ...

    @abstractmethod
    async def get_permissions(
        self,
        user: UserContextABC,
        resource: ObjectRef,
    ) -> List[str]:
        """List effective permissions for a user on a resource.

        Parameters
        ----------
        user : UserContextABC
            Current user context.
        resource : ObjectRef
            Resource to evaluate.

        Returns
        -------
        List[str]
            Granted permissions.
        """
        ...

    
class NotePermissionRepoSpicedb(NotePermissionRepo):
    converter = SpicedbPermissionConverter()
    _default_permission_candidates_by_object_type = {
        "note": ["view", "write", "delete"],
        "directory": ["view", "write", "delete"],
    }

    def __init__(
        self,
        client: AsyncClient,
        permission_candidates_by_object_type: dict[str, list[str]] | None = None,
    ) -> None:
        self.client = client
        self._permission_candidates_by_object_type = (
            permission_candidates_by_object_type
            if permission_candidates_by_object_type is not None
            else self._default_permission_candidates_by_object_type
        )

    async def insert(self, relationships: List[Relationship]) -> List[Relationship]:
        requests = [ImportBulkRelationshipsRequest(
            relationships=[self.converter.convert_relationship(rel) for rel in relationships]
        )]
        await self.client.ImportBulkRelationships((req for req in requests))
        return relationships

    async def delete(self, relationship: Relationship) -> Relationship:
        # spicedb does not support bulk delete, so we need to delete one by one
        deleted_relationships = []
        filter = RelationshipFilter()
        
        def get_filter(rel: Relationship) -> RelationshipFilter:
            assert rel.resource.object_id != UNDEFINED, "object_id must be provided for delete operation"
            assert rel.subject.object_id != UNDEFINED, "subject_id must be provided for delete operation"
            
            filter = RelationshipFilter(
                resource_type=rel.resource.object_type,
                optional_resource_id=str(rel.resource.object_id),
                optional_relation=rel.relation,
                optional_subject_filter=SubjectFilter(
                    subject_type=rel.subject.object_type,
                    optional_subject_id=str(rel.subject.object_id)
                )
            )
            return filter
       
        
        result = await self.client.DeleteRelationships(
            DeleteRelationshipsRequest(
                relationship_filter=get_filter(relationship)
            )
        )
        
        assert result.DELETION_PROGRESS_COMPLETE
        return relationship

    async def lookup(self, relationship: Relationship) -> List[ObjectRef]:
        # spicedb does not support bulk lookup, so we need to lookup one by one
        filter = RelationshipFilter(
            optional_subject_filter=SubjectFilter(
                subject_type=relationship.subject.object_type,
                optional_subject_id=str(relationship.subject.object_id)
            )
        )
        filter.optional_relation = relationship.relation
        filter.resource_type = relationship.resource.object_type
        if relationship.resource.object_id != UNDEFINED:
            filter.optional_resource_id = str(relationship.resource.object_id)
        if relationship.subject.object_id != UNDEFINED:
            filter.optional_subject_filter
        
        result = self.client.LookupResources(
            LookupResourcesRequest(
                resource_object_type=relationship.resource.object_type,
                permission=relationship.relation,
                subject=self.converter.convert_subject_ref(relationship.subject),
                consistency=Consistency(fully_consistent=True)
            )   
        )
        objects: List[ObjectRef] = []
        async for resp in result:
            objects.append(
                ObjectRef(
                    object_type=ObjectTypeEnum(str(relationship.resource.object_type)),
                    object_id=resp.resource_object_id
                )
            )
        return objects


    async def lookup_relationships(self, relationship: Relationship) -> List[Relationship]:
        subject_filter = SubjectFilter(subject_type=relationship.subject.object_type)
        if relationship.subject.object_id != UNDEFINED:
            subject_filter.optional_subject_id = str(relationship.subject.object_id)

        relation_filter = RelationshipFilter(
            resource_type=relationship.resource.object_type,
            optional_relation=relationship.relation,
            optional_subject_filter=subject_filter,
        )
        if relationship.resource.object_id != UNDEFINED:
            relation_filter.optional_resource_id = str(relationship.resource.object_id)

        response_stream = self.client.ExportBulkRelationships(
            ExportBulkRelationshipsRequest(optional_relationship_filter=relation_filter)
        )

        relationships: List[Relationship] = []
        async for response in response_stream:
            for stored in response.relationships:
                relationships.append(
                    Relationship(
                        resource=ObjectRef(
                            object_type=ObjectTypeEnum(stored.resource.object_type),
                            object_id=stored.resource.object_id,
                        ),
                        relation=cast(RelationName, stored.relation),
                        subject=SubjectRef(
                            object_type=ObjectTypeEnum(stored.subject.object.object_type),
                            object_id=stored.subject.object.object_id,
                        ),
                    )
                )
        return relationships

    async def lookup_notes(self, user: UserContextABC, permission: str) -> List[ObjectRef]:
        user_id = user.user_id
        relationship = Relationship(
            resource=ObjectRef(
                object_type="note",
                object_id=UNDEFINED
            ),
            relation=NoteRelationEnum(permission),
            subject=SubjectRef(
                object_type="user",
                object_id=user_id
            )
        )
        return await self.lookup(relationship)

    async def list_relationships(self, resource: ObjectRef) -> List[Relationship]:
        relation_filter = RelationshipFilter(resource_type=resource.object_type)
        if resource.object_id != UNDEFINED:
            relation_filter.optional_resource_id = str(resource.object_id)

        response_stream = self.client.ExportBulkRelationships(
            ExportBulkRelationshipsRequest(optional_relationship_filter=relation_filter)
        )

        relationships: List[Relationship] = []
        async for response in response_stream:
            for relationship in response.relationships:
                relationships.append(
                    Relationship(
                        resource=ObjectRef(
                            object_type=ObjectTypeEnum(relationship.resource.object_type),
                            object_id=relationship.resource.object_id,
                        ),
                        relation=cast(RelationName, relationship.relation),
                        subject=SubjectRef(
                            object_type=ObjectTypeEnum(relationship.subject.object.object_type),
                            object_id=relationship.subject.object.object_id,
                        ),
                    )
                )

        return relationships

    async def has_permission(
        self,
        user: UserContextABC,
        permission: str,
        resource: ObjectRef,
    ) -> bool:
        assert resource.object_id != UNDEFINED, "object_id must be provided for permission checks"

        response = await self.client.CheckPermission(
            CheckPermissionRequest(
                resource=self.converter.convert_object_ref(resource),
                permission=permission,
                subject=self.converter.convert_subject_ref(
                    SubjectRef(
                        object_type="user",
                        object_id=user.user_id,
                    )
                ),
                consistency=Consistency(fully_consistent=True),
            )
        )
        return response.permissionship == CheckPermissionResponse.PERMISSIONSHIP_HAS_PERMISSION

    async def get_permissions(self, user: UserContextABC, resource: ObjectRef) -> List[str]:
        assert resource.object_id != UNDEFINED, "object_id must be provided for permission checks"

        candidates = self._permission_candidates_by_object_type.get(resource.object_type, [])
        permissions: List[str] = []
        for permission in candidates:
            if await self.has_permission(user=user, permission=permission, resource=resource):
                permissions.append(permission)
        return permissions



class NotePermissionRepoInMemory(NotePermissionRepo):
    """In-memory implementation of NotePermissionRepo for unit testing.

    This test double simulates a small, deterministic subset of SpiceDB behavior:
    - Stores explicit relationships exactly as written.
    - Resolves implied permissions from direct relations (for example owner -> view).
    - Supports resource lookup by effective permission for note objects.
    It intentionally does not simulate Zanzibar graph traversal semantics in full.
    """
    _relation_implied_permissions = {
        "note": {
            "admin": {"admin", "delete", "write", "view"},
            "writer": {"writer", "write", "view"},
            "reader": {"reader", "view"},
            "owner": {"owner", "admin", "delete", "write", "view"},
        },
        "directory": {
            "admin": {"admin", "delete", "write", "view"},
            "writer": {"writer", "write", "view"},
            "reader": {"reader", "view"},
        },
    }

    def __init__(self) -> None:
        self._store: List[Relationship] = []

    async def insert(self, relationships: List[Relationship]) -> List[Relationship]:
        self._store.extend(deepcopy(relationships))
        return relationships

    async def delete(self, relationship: Relationship) -> Relationship:
        def matches(stored: Relationship) -> bool:
            obj_match = (
                stored.resource.object_type == relationship.resource.object_type
                and (
                    relationship.resource.object_id is UNDEFINED
                    or stored.resource.object_id == relationship.resource.object_id
                )
            )
            rel_match = stored.relation == relationship.relation
            subj_match = (
                stored.subject.object_type == relationship.subject.object_type
                and (
                    relationship.subject.object_id is UNDEFINED
                    or stored.subject.object_id == relationship.subject.object_id
                )
            )
            return obj_match and rel_match and subj_match

        self._store = [r for r in self._store if not matches(r)]
        return relationship

    async def lookup(self, relationship: Relationship) -> List[ObjectRef]:
        # Simulates a direct relationship lookup (equivalent to filtering stored tuples),
        # not transitive permission expansion.
        results: List[ObjectRef] = []
        for stored in self._store:
            obj_match = (
                stored.resource.object_type == relationship.resource.object_type
                and (
                    relationship.resource.object_id is UNDEFINED
                    or stored.resource.object_id == relationship.resource.object_id
                )
            )
            rel_match = stored.relation == relationship.relation
            subj_match = (
                stored.subject.object_type == relationship.subject.object_type
                and (
                    relationship.subject.object_id is UNDEFINED
                    or stored.subject.object_id == relationship.subject.object_id
                )
            )
            if obj_match and rel_match and subj_match:
                results.append(
                    ObjectRef(
                        object_type=ObjectTypeEnum(str(stored.resource.object_type)),
                        object_id=stored.resource.object_id
                    )
                )
        return results

    async def lookup_relationships(self, relationship: Relationship) -> List[Relationship]:
        relationships: List[Relationship] = []
        for stored in self._store:
            obj_match = (
                stored.resource.object_type == relationship.resource.object_type
                and (
                    relationship.resource.object_id is UNDEFINED
                    or stored.resource.object_id == relationship.resource.object_id
                )
            )
            rel_match = stored.relation == relationship.relation
            subj_match = (
                stored.subject.object_type == relationship.subject.object_type
                and (
                    relationship.subject.object_id is UNDEFINED
                    or stored.subject.object_id == relationship.subject.object_id
                )
            )
            if obj_match and rel_match and subj_match:
                relationships.append(deepcopy(stored))
        return relationships

    async def lookup_relationships(self, relationship: Relationship) -> List[Relationship]:
        relationships: List[Relationship] = []
        for stored in self._store:
            obj_match = (
                stored.resource.object_type == relationship.resource.object_type
                and (
                    relationship.resource.object_id is UNDEFINED
                    or stored.resource.object_id == relationship.resource.object_id
                )
            )
            rel_match = stored.relation == relationship.relation
            subj_match = (
                stored.subject.object_type == relationship.subject.object_type
                and (
                    relationship.subject.object_id is UNDEFINED
                    or stored.subject.object_id == relationship.subject.object_id
                )
            )
            if obj_match and rel_match and subj_match:
                relationships.append(deepcopy(stored))
        return relationships

    async def lookup_notes(self, user: UserContextABC, permission: str) -> List[ObjectRef]:
        # Simulates SpiceDB LookupResources for notes by checking effective permissions
        # derived from each stored direct relation for the current user.
        user_id = user.user_id
        matched: dict[str, ObjectRef] = {}
        note_implied = self._relation_implied_permissions.get("note", {})
        requested_permission = str(permission)

        for stored in self._store:
            if str(stored.resource.object_type) != ObjectTypeEnum.NOTE.value:
                continue
            if str(stored.subject.object_type) != ObjectTypeEnum.USER.value or stored.subject.object_id != user_id:
                continue

            stored_relation = str(stored.relation)
            implied_permissions = note_implied.get(stored_relation, {stored_relation})
            if requested_permission in implied_permissions and isinstance(stored.resource.object_id, str):
                matched[stored.resource.object_id] = ObjectRef(
                    object_type=ObjectTypeEnum.NOTE,
                    object_id=stored.resource.object_id,
                )

        return list(matched.values())

    async def list_relationships(self, resource: ObjectRef) -> List[Relationship]:
        relationships: List[Relationship] = []
        for stored in self._store:
            if (
                stored.resource.object_type == resource.object_type
                and (
                    resource.object_id is UNDEFINED
                    or stored.resource.object_id == resource.object_id
                )
            ):
                relationships.append(deepcopy(stored))

        return relationships

    async def has_permission(
        self,
        user: UserContextABC,
        permission: str,
        resource: ObjectRef,
    ) -> bool:
        permissions = await self.get_permissions(user=user, resource=resource)
        return permission in permissions

    async def get_permissions(self, user: UserContextABC, resource: ObjectRef) -> List[str]:
        assert resource.object_id != UNDEFINED, "object_id must be provided for permission checks"

        # Collect direct relationships for this user-resource pair,
        # then expand to effective permissions via the static implication map.
        direct_relations: List[str] = []
        for stored in self._store:
            if (
                stored.resource.object_type == resource.object_type
                and stored.resource.object_id == resource.object_id
                and stored.subject.object_type == "user"
                and stored.subject.object_id == user.user_id
            ):
                direct_relations.append(stored.relation)

        implied_map = self._relation_implied_permissions.get(resource.object_type, {})
        permissions = set[str]()
        for relation in direct_relations:
            # Keep unknown relations as-is so tests can still work with custom schemas.
            permissions.update(implied_map.get(relation, {relation}))

        return sorted(permissions)

