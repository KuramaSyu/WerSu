from abc import ABC, abstractmethod
from copy import deepcopy
from enum import StrEnum
from operator import ge
import re
from typing import Any, List, Literal, Optional, Protocol, TypeAlias, cast
from functools import wraps
import inspect

from asyncpg import Record
from authzed.api.v1.permission_service_pb2 import ExportBulkRelationshipsRequest, ImportBulkRelationshipsRequest
import grpc
from src.api.permission_repo import PermissionRepoABC
from src.api.service_unavailable_error import ServiceUnavailableError
from src.api import PermissionConverterABC, ObjectRef, SubjectRef, RelationEnum, Relationship, UserContextABC, ObjectTypeEnum, RelationName, NoteRelationEnum
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

class SpicedbPermissionConverter(PermissionConverterABC):
    """Adapter to convert between domain Relationship and SpiceDB Relationship"""

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
    
def handle_error(func):
    """Decorator for instance methods that wraps exceptions using `self._wrap_grpc_error`.

    Works with async and sync methods.
    """
    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def _async_wrapper(self, *args, **kwargs):
            try:
                return await func(self, *args, **kwargs)
            except Exception as e:
                if isinstance(e, grpc.aio.AioRpcError) and e.code() == grpc.StatusCode.UNAVAILABLE:
                    address = None
                    client = getattr(self, "client", None)
                    if client is not None:
                        # try known channel locations safely
                        ch = getattr(client, "_channel", None)
                        try:
                            if ch is not None and hasattr(ch, "target"):
                                address = ch.target()
                        except Exception:
                            address = None

                        if address is None:
                            trans = getattr(client, "_transport", None)
                            if trans is not None:
                                ch2 = getattr(trans, "_channel", None)
                                try:
                                    if ch2 is not None and hasattr(ch2, "target"):
                                        address = ch2.target()
                                except Exception:
                                    address = None

                        if address is None:
                            maybe_target = getattr(client, "target", None)
                            if isinstance(maybe_target, str):
                                address = maybe_target

                    raise ServiceUnavailableError(name="SpiceDB", address=str(address))
                raise

        return _async_wrapper

    @wraps(func)
    def _sync_wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            if isinstance(e, grpc.aio.AioRpcError) and e.code() == grpc.StatusCode.UNAVAILABLE:
                address = None
                client = getattr(self, "client", None)
                if client is not None:
                    ch = getattr(client, "_channel", None)
                    try:
                        if ch is not None and hasattr(ch, "target"):
                            address = ch.target()
                    except Exception:
                        address = None

                    if address is None:
                        trans = getattr(client, "_transport", None)
                        if trans is not None:
                            ch2 = getattr(trans, "_channel", None)
                            try:
                                if ch2 is not None and hasattr(ch2, "target"):
                                    address = ch2.target()
                            except Exception:
                                address = None

                    if address is None:
                        maybe_target = getattr(client, "target", None)
                        if isinstance(maybe_target, str):
                            address = maybe_target

                raise ServiceUnavailableError(name="SpiceDB", address=str(address))
            raise

    return _sync_wrapper


class NotePermissionRepoSpicedb(PermissionRepoABC):
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

    
    def _wrap_grpc_error(self, error: Exception) -> Exception:
        """
        Wraps gRPC errors in a ServiceUnavailableError if they indicate that SpiceDB is unavailable.
        """
        if isinstance(error, grpc.aio.AioRpcError) and error.code() == grpc.StatusCode.UNAVAILABLE:
            return ServiceUnavailableError(name="SpiceDB", address=self.client._channel.target())  # type: ignore
        return error

    @handle_error
    async def insert(self, relationships: List[Relationship]) -> List[Relationship]:
        # SpiceDB bulk import API consumes a request stream; we send a single batched request.
        requests = [ImportBulkRelationshipsRequest(
            relationships=[self.converter.convert_relationship(rel) for rel in relationships]
        )]
        await self.client.ImportBulkRelationships((req for req in requests))
        return relationships

    @handle_error
    async def delete(self, relationship: Relationship) -> Relationship:
        # spicedb does not support bulk delete, so we need to delete one by one
        deleted_relationships = []
        filter = RelationshipFilter()

        def get_filter(rel: Relationship) -> RelationshipFilter:
            # build a filter, where nearly everything can be a wildcard, when UNDEFINED is provided
            filter = RelationshipFilter()
            filter.resource_type = rel.resource.object_type
            if rel.resource.object_id != UNDEFINED:
                filter.optional_resource_id = str(rel.resource.object_id)
            if rel.relation != UNDEFINED:
                filter.optional_relation = rel.relation
            if rel.subject.object_type != UNDEFINED:
                filter.optional_subject_filter.subject_type = rel.subject.object_type
            if rel.subject.object_id != UNDEFINED:
                filter.optional_subject_filter.optional_subject_id = str(rel.subject.object_id)
            return filter


        result = await self.client.DeleteRelationships(
            DeleteRelationshipsRequest(
                relationship_filter=get_filter(relationship)
            )
        )

        assert result.DELETION_PROGRESS_COMPLETE
        return relationship

    @handle_error
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
        
        # LookupResources resolves effective permission, not only direct tuples.
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

    async def check(self, relationship: Relationship) -> bool:
        converted = self.converter.convert_relationship(relationship)
        response = await self.client.CheckPermission(CheckPermissionRequest(
            resource=converted.resource,
            permission=converted.relation,
            subject=converted.subject,
        ))
        return response.permissionship == CheckPermissionResponse.PERMISSIONSHIP_HAS_PERMISSION


    @handle_error
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
            # Export API is paged/streamed, so flatten every message into the result list.
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

    @handle_error
    async def list_relationships(self, resource: ObjectRef) -> List[Relationship]:
        relation_filter = RelationshipFilter(resource_type=resource.object_type)
        if resource.object_id != UNDEFINED:
            relation_filter.optional_resource_id = str(resource.object_id)

        # Export direct tuples for a resource type/id and map them back to domain entities.
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

    @handle_error
    async def get_permissions(self, user: UserContextABC, resource: ObjectRef) -> List[str]:
        assert resource.object_id != UNDEFINED, "object_id must be provided for permission checks"

        candidates = self._permission_candidates_by_object_type.get(resource.object_type, [])
        permissions: List[str] = []
        for permission in candidates:
            # Evaluate candidate permissions one by one through SpiceDB CheckPermission.
            if await self.has_permission(user=user, permission=permission, resource=resource):
                permissions.append(permission)
        return permissions



class NotePermissionRepoInMemory(PermissionRepoABC):
    """In-memory implementation of NotePermissionRepo for unit testing.

    This test double simulates a small, deterministic subset of SpiceDB behavior:
    - Stores explicit relationships exactly as written.
    - Resolves implied permissions from direct relations (for example owner -> view).
    - Supports resource lookup by effective permission for note objects.
    It intentionally does not simulate Zanzibar graph traversal semantics in full.
    """
    _relation_implied_permissions = {
        "note": {
            "admin": {"admin", "delete", "write", "view", "edit_permissions"},
            "writer": {"writer", "write", "view"},
            "reader": {"reader", "view"},
            "owner": {"owner", "admin", "delete", "write", "view", "edit_permissions"},
        },
        "directory": {
            "admin": {"admin", "delete", "write", "view", "edit_permissions"},
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

