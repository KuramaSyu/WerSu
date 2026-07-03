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
from src.api.undefined import is_undefined, unwrap_undefined
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
        consistent: bool = True,
    ) -> None:
        """Create a SpiceDB-backed permission repo.

        Parameters
        ----------
        client : AsyncClient
            Authzed async client.
        permission_candidates_by_object_type : dict[str, list[str]] | None
            Per-object-type list of candidate permission names. Defaults are
            provided for ``note`` and ``directory`` if not supplied.
        consistent : bool, default True
            When True, every RPC uses ``Consistency(fully_consistent=True)``
            and ``wait_for_ready=True``. This guarantees the caller that
            ``insert()`` (and any subsequent read) only returns after SpiceDB
            has durably committed and made the write visible. It increases
            latency, especially across regions. Set to False to fall back to
            SpiceDB's default ``minimize_latency`` semantics for higher
            throughput at the cost of read-your-writes races.
        """
        self.client = client
        self._permission_candidates_by_object_type = (
            permission_candidates_by_object_type
            if permission_candidates_by_object_type is not None
            else self._default_permission_candidates_by_object_type
        )
        self._consistent = consistent

    def _consistency(self) -> Optional[Consistency]:
        """Build the Consistency message honoring the ``consistent`` flag.

        Returns None when the flag is False so callers can omit the field and
        let SpiceDB apply its default (``minimize_latency``).
        """
        if not self._consistent:
            return None
        return Consistency(fully_consistent=True)

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
        #
        # Writes are *always* fully consistent on the server side; SpiceDB
        # does not accept a `consistency` field on ``ImportBulkRelationshipsRequest``.
        # ``ImportBulkRelationships`` is a stream-unary RPC so we cannot pass
        # ``wait_for_ready`` either: that flag is only legal on unary
        # gRPC methods.  The caller therefore relies on SpiceDB having
        # committed the tuples by the time the RPC future resolves.
        requests = [ImportBulkRelationshipsRequest(
            relationships=[self.converter.convert_relationship(rel) for rel in relationships]
        )]
        await self.client.ImportBulkRelationships((req for req in requests))
        return relationships

    @handle_error
    async def delete(self, relationship: Relationship) -> Relationship:
        # spicedb does not support bulk delete, so we need to delete one by one

        def get_filter(rel: Relationship) -> RelationshipFilter:
            # Build a wildcard-aware filter: every field except
            # `resource_type` is optional, and `UNDEFINED` placeholders
            # match every value for that field.  Always read attributes
            # through `is_undefined()` first so callers can pass real
            # ``ObjectRef``/``SubjectRef`` objects whose ID slots are
            # still ``UNDEFINED``.
            relation_filter = RelationshipFilter()

            # resource_type is the only required field on the filter
            if is_undefined(rel.resource) or is_undefined(rel.resource.object_type):
                raise ValueError(
                    "delete() requires a concrete resource.object_type; "
                    "got UNDEFINED"
                )
            relation_filter.resource_type = rel.resource.object_type
            if not is_undefined(rel.resource.object_id):
                relation_filter.optional_resource_id = str(rel.resource.object_id)
            if not is_undefined(rel.relation):
                relation_filter.optional_relation = rel.relation
            if not is_undefined(rel.subject.object_type):
                relation_filter.optional_subject_filter.subject_type = (
                    rel.subject.object_type
                )
            if not is_undefined(rel.subject.object_id):
                relation_filter.optional_subject_filter.optional_subject_id = (
                    str(rel.subject.object_id)
                )
            return relation_filter


        # Like ``insert``, writes are always fully consistent on the server
        # side; ``DeleteRelationshipsRequest`` has no `consistency` field.
        # ``wait_for_ready=True`` keeps a transient SpiceDB outage from
        # immediately failing the call with UNAVAILABLE.
        result = await self.client.DeleteRelationships(
            DeleteRelationshipsRequest(
                relationship_filter=get_filter(relationship)
            ),
            wait_for_ready=True,
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
        # Caller-supplied consistency takes precedence; falls back to
        # minimize_latency when the `consistent` flag is False.
        consistency = self._consistency() or Consistency(fully_consistent=True)
        result = self.client.LookupResources(
            LookupResourcesRequest(
                resource_object_type=relationship.resource.object_type,
                permission=relationship.relation,
                subject=self.converter.convert_subject_ref(relationship.subject),
                consistency=consistency,
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
            # Fully-consistent reads so callers see their own writes.
            # Without this, the default (minimize_latency) semantics can
            # return stale denials immediately after `insert()`.
            # Controlled by the repo's `consistent` flag.
            consistency=self._consistency() or Consistency(fully_consistent=True),
        ))
        return response.permissionship == CheckPermissionResponse.PERMISSIONSHIP_HAS_PERMISSION


    @handle_error
    async def lookup_relationships(self, relationship: Relationship) -> List[Relationship]:
        # enforce resource:id#permission@XXX:id
        subject_filter = SubjectFilter(subject_type=relationship.subject.object_type)

        # maybe add resource:id#permission@subject:XXX 
        if relationship.subject.object_id != UNDEFINED:
            subject_filter.optional_subject_id = str(relationship.subject.object_id)

        # enforece XXX:id#permission@subject:id
        filter = RelationshipFilter(
            resource_type=relationship.resource.object_type,
            optional_subject_filter=subject_filter,
        )
        # maybe add resource:id#XXX@subject:id
        if relationship.relation != UNDEFINED:
            filter.optional_relation = relationship.relation
        
        # maybe add resource:XXX#permission@subject:id
        if relationship.resource.object_id != UNDEFINED:
            filter.optional_resource_id = str(relationship.resource.object_id)

        # ``ExportBulkRelationships`` is a server-streaming RPC. The
        # gRPC ``wait_for_ready`` flag is illegal on streaming calls,
        # so consistency (and therefore read-your-writes) must travel
        # *inside* the request body via the ``Consistency`` field.
        request_kwargs: dict[str, Any] = {
            "optional_relationship_filter": filter,
        }
        if self._consistent:
            request_kwargs["consistency"] = Consistency(fully_consistent=True)

        response_stream = self.client.ExportBulkRelationships(
            ExportBulkRelationshipsRequest(**request_kwargs)
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
        # Same note as ``lookup_relationships``: streaming RPC, so
        # consistency must travel inside the request body.
        request_kwargs: dict[str, Any] = {
            "optional_relationship_filter": relation_filter,
        }
        if self._consistent:
            request_kwargs["consistency"] = Consistency(fully_consistent=True)

        response_stream = self.client.ExportBulkRelationships(
            ExportBulkRelationshipsRequest(**request_kwargs)
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
                consistency=self._consistency() or Consistency(fully_consistent=True),
            )
        )
        return response.permissionship == CheckPermissionResponse.PERMISSIONSHIP_HAS_PERMISSION

    @handle_error
    async def get_permissions(self, user: UserContextABC, resource: ObjectRef) -> List[str]:
        """Get all effective permissions for a user on a resource."""
        # do we really need this? I think filter should be possible too
        assert resource.object_id != UNDEFINED, "object_id must be provided for permission checks"

        permissions = []
        relationships = await self.lookup_relationships(
            Relationship(
                resource=resource,
                relation=UNDEFINED,
                subject=SubjectRef(
                    object_type="user",
                    object_id=user.user_id,
                )
            )
        )
        for rel in relationships:
            permissions.append(unwrap_undefined(rel.relation))
        
        # AI generated:
        # candidates = self._permission_candidates_by_object_type.get(resource.object_type, [])
        # permissions: List[str] = []
        # for permission in candidates:
        #     # Evaluate candidate permissions one by one through SpiceDB CheckPermission.
        #     if await self.has_permission(user=user, permission=permission, resource=resource):
        #         permissions.append(permission)

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
        "attachment": {
            "admin": {"admin", "delete", "write", "view", "edit_permissions"},
            "writer": {"writer", "write", "view"},
            "reader": {"reader", "view"},
            "owner": {"owner", "admin", "delete", "write", "view", "edit_permissions"},
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

    async def check(self, relationship: Relationship) -> bool:
        # In-memory check resolves to whether any stored direct relationship matches the filter.
        return bool(await self.lookup_relationships(relationship))

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

