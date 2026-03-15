from abc import ABC, abstractmethod
from copy import deepcopy
from operator import ge
import re
from typing import Any, List, Protocol

from asyncpg import Record
from authzed.api.v1.permission_service_pb2 import ImportBulkRelationshipsRequest
from src.api.user_context import UserContextABC
from src.db.entities import NotePermissionEntity
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

class ObjectRef:
    def __init__(self, object_type: str, object_id: UndefinedOr[str]) -> None:
        self.object_type = object_type
        self.object_id = object_id
      

class SubjectRef(ObjectRef):
    pass

class Relationship:
    def __init__(self, resource: ObjectRef, relation: str, subject: SubjectRef) -> None:
        self.resource = resource
        self.relation = relation
        self.subject = subject

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
                    object_type=relationship.resource.object_type,
                    object_id=resp.resource_object_id
                )
            )
        return objects

    async def lookup_notes(self, user: UserContextABC, permission: str) -> List[ObjectRef]:
        user_id = user.user_id
        relationship = Relationship(
            resource=ObjectRef(
                object_type="note",
                object_id=UNDEFINED
            ),
            relation=permission,
            subject=SubjectRef(
                object_type="user",
                object_id=user_id
            )
        )
        return await self.lookup(relationship)

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
            Resource to check.

        Returns
        -------
        bool
            True if granted by SpiceDB.
        """
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
            Permissions granted by configured candidates.
        """
        assert resource.object_id != UNDEFINED, "object_id must be provided for permission checks"

        candidates = self._permission_candidates_by_object_type.get(resource.object_type, [])
        permissions: List[str] = []
        for permission in candidates:
            if await self.has_permission(user=user, permission=permission, resource=resource):
                permissions.append(permission)
        return permissions



class NotePermissionRepoInMemory(NotePermissionRepo):
    """In-memory implementation of NotePermissionRepo for unit testing."""
    _relation_implied_permissions = {
        "note": {
            "admin": {"admin", "delete", "write", "view"},
            "writer": {"writer", "write", "view"},
            "reader": {"reader", "view"},
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
                        object_type=stored.resource.object_type,
                        object_id=stored.resource.object_id
                    )
                )
        return results

    async def lookup_notes(self, user: UserContextABC, permission: str) -> List[ObjectRef]:
        user_id = user.user_id
        relationship = Relationship(
            resource=ObjectRef(object_type="note", object_id=UNDEFINED),
            relation=permission,
            subject=SubjectRef(object_type="user", object_id=user_id),
        )
        return await self.lookup(relationship)

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
            Resource to check.

        Returns
        -------
        bool
            True when permission is present.
        """
        permissions = await self.get_permissions(user=user, resource=resource)
        return permission in permissions

    async def get_permissions(self, user: UserContextABC, resource: ObjectRef) -> List[str]:
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
            Sorted unique permission names.
        """
        assert resource.object_id != UNDEFINED, "object_id must be provided for permission checks"

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

