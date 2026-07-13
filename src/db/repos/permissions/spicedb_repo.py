from abc import ABC, abstractmethod
from copy import deepcopy
from enum import StrEnum
from operator import ge
import re
from typing import Any, List, Literal, Optional, Protocol, TypeAlias, cast
from functools import wraps
import inspect

from asyncpg import Record
from authzed.api.v1.permission_service_pb2 import ExportBulkRelationshipsRequest, ImportBulkRelationshipsRequest, LookupResourcesRequest, LookupSubjectsRequest
import grpc
from src.api.permission_repo import PermissionRepoABC
from src.api.service_unavailable_error import ServiceUnavailableError
from src.api import PermissionConverterABC, ObjectRef, SubjectRef, RelationEnum, Relationship, UserContextABC, ObjectTypeEnum, RelationName, NoteRelationEnum
from src.api.relationship import AttachmentRelationEnum, DirectoryRelationEnum
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


class SpicedbPermissionRepo(PermissionRepoABC):
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
        directory_subdirectory_table: Optional[TableABC] = None,
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
        directory_subdirectory_table : TableABC, optional
            Optional Postgres ``Table`` for
            ``note.directory_subdirectory``.  Kept on the repo so
            the directory subtree walks in
            :meth:`DirectoryRepoFacade.resolve_subtree` /
            :meth:`DirectoryRepoFacade.list_note_directory_ids`
            can target Postgres directly.  Optional -- the repo
            still works without it, falling back to
            :meth:`lookup_relationships` for the same shapes.
        """
        self.client = client
        self._permission_candidates_by_object_type = (
            permission_candidates_by_object_type
            if permission_candidates_by_object_type is not None
            else self._default_permission_candidates_by_object_type
        )
        self._consistent = consistent
        self._directory_subdirectory_table = directory_subdirectory_table

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
    async def lookup(self, relationship: Relationship) -> List[str]:
        """Dispatch to the matching lookup and return just the ids.

        Exactly one of ``relationship.resource.object_id`` and
        ``relationship.subject.object_id`` must be
        :obj:`~src.api.undefined.UNDEFINED`; the other side must be a
        concrete id.  Anything else raises :exc:`ValueError`.
        """
        resource_id_undefined = is_undefined(relationship.resource.object_id)
        subject_id_undefined = is_undefined(relationship.subject.object_id)
        if resource_id_undefined == subject_id_undefined:
            raise ValueError(
                "lookup() requires exactly one of "
                "relationship.resource.object_id / "
                "relationship.subject.object_id to be UNDEFINED"
            )

        if resource_id_undefined:
            refs = await self._lookup_resources(relationship)
            return [str(ref.object_id) for ref in refs]
        refs = await self._lookup_subjects(relationship)
        return [str(ref.object_id) for ref in refs]

    async def _lookup_resources(self, relationship: Relationship) -> List[ObjectRef]:
        """SpiceDB ``LookupResources`` adapter.

        ``relationship.resource.object_id`` must be
        :obj:`~src.api.undefined.UNDEFINED`; the rest of the triple
        narrows the result set.
        """
        relation = unwrap_undefined(relationship.relation)
        resource_type = unwrap_undefined(relationship.resource.object_type)

        consistency = self._consistency() or Consistency(fully_consistent=True)
        result = self.client.LookupResources(
            LookupResourcesRequest(
                resource_object_type=resource_type,
                permission=relation,
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

    async def _lookup_subjects(self, relationship: Relationship) -> List[ObjectRef]:
        """SpiceDB ``LookupSubjects`` adapter.

        ``relationship.subject.object_id`` must be
        :obj:`~src.api.undefined.UNDEFINED`; the resource side and
        ``relation`` narrow the result set.
        """
        relation = unwrap_undefined(relationship.relation)
        if is_undefined(relationship.resource.object_id):
            raise ValueError(
                "_lookup_subjects() requires a concrete "
                "relationship.resource.object_id"
            )

        consistency = self._consistency() or Consistency(fully_consistent=True)
        result = self.client.LookupSubjects(
            LookupSubjectsRequest(
                resource=self.converter.convert_object_ref(relationship.resource),
                permission=relation,
                subject_object_type=unwrap_undefined(relationship.subject.object_type),
                consistency=consistency,
            )
        )
        objects: List[ObjectRef] = []
        async for resp in result:
            objects.append(
                ObjectRef(
                    object_type=ObjectTypeEnum(str(relationship.subject.object_type)),
                    object_id=resp.subject.object.object_id,
                )
            )
        return objects

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

    @handle_error
    async def resolve_children(
        self,
        directory_id: str,
        *,
        max_depth: int = 10,
        exclusive: bool = True,
    ) -> "ResolvedChildren":
        """Walk a directory subtree over SpiceDB and collect ids.

        Mirrors the in-memory implementation's algorithm but uses
        ``ExportBulkRelationships`` instead of scanning ``self._store``.
        """

        from src.api.permission_repo import ResolvedChildren

        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")

        root = str(directory_id)
        consistency_kwargs: dict[str, Any] = {}
        if self._consistent:
            consistency_kwargs["consistency"] = Consistency(fully_consistent=True)

        # 1. Walk directory#parent@directory to collect every
        #    reachable directory id (root included).
        sub_directory_ids: set[str] = {root}
        queue: list[tuple[str, int]] = [(root, 0)]
        while queue:
            current_id, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            response_stream = self.client.ExportBulkRelationships(
                ExportBulkRelationshipsRequest(
                    optional_relationship_filter=RelationshipFilter(
                        resource_type=ObjectTypeEnum.DIRECTORY.value,
                        optional_relation=DirectoryRelationEnum.PARENT,
                        optional_subject_filter=SubjectFilter(
                            subject_type=ObjectTypeEnum.DIRECTORY.value,
                            optional_subject_id=current_id,
                        ),
                    ),
                    **consistency_kwargs,
                )
            )
            async for response in response_stream:
                for rel in response.relationships:
                    if rel.resource.object_id in (UNDEFINED, None, ""):
                        continue
                    child_id = str(rel.resource.object_id)
                    if child_id not in sub_directory_ids:
                        sub_directory_ids.add(child_id)
                        queue.append((child_id, depth + 1))

        # 2. Collect every note that has at least one
        #    ``note#parent_directory@directory`` relation pointing
        #    into the subtree.
        note_ids: set[str] = set()
        for directory_id in list(sub_directory_ids):
            response_stream = self.client.ExportBulkRelationships(
                ExportBulkRelationshipsRequest(
                    optional_relationship_filter=RelationshipFilter(
                        resource_type=ObjectTypeEnum.NOTE.value,
                        optional_relation=NoteRelationEnum.PARENT_DIRECTORY,
                        optional_subject_filter=SubjectFilter(
                            subject_type=ObjectTypeEnum.DIRECTORY.value,
                            optional_subject_id=directory_id,
                        ),
                    ),
                    **consistency_kwargs,
                )
            )
            async for response in response_stream:
                for rel in response.relationships:
                    if rel.resource.object_id in (UNDEFINED, None, ""):
                        continue
                    note_ids.add(str(rel.resource.object_id))

        # 3. Same for attachments.
        attachment_ids: set[str] = set()
        for note_id in list(note_ids):
            response_stream = self.client.ExportBulkRelationships(
                ExportBulkRelationshipsRequest(
                    optional_relationship_filter=RelationshipFilter(
                        resource_type=ObjectTypeEnum.ATTACHMENT.value,
                        optional_relation=AttachmentRelationEnum.PARENT_NOTE,
                        optional_subject_filter=SubjectFilter(
                            subject_type=ObjectTypeEnum.NOTE.value,
                            optional_subject_id=note_id,
                        ),
                    ),
                    **consistency_kwargs,
                )
            )
            async for response in response_stream:
                for rel in response.relationships:
                    if rel.resource.object_id in (UNDEFINED, None, ""):
                        continue
                    attachment_ids.add(str(rel.resource.object_id))

        if exclusive:
            # Drop notes whose only parents aren't all inside the
            # subtree, and attachments similarly.  Pull the full
            # list of parent relations for each candidate and check
            # set membership.
            exclusive_notes: set[str] = set()
            for note_id in note_ids:
                response_stream = self.client.ExportBulkRelationships(
                    ExportBulkRelationshipsRequest(
                        optional_relationship_filter=RelationshipFilter(
                            resource_type=ObjectTypeEnum.NOTE.value,
                            optional_resource_id=note_id,
                            optional_relation=NoteRelationEnum.PARENT_DIRECTORY,
                        ),
                        **consistency_kwargs,
                    )
                )
                parents: set[str] = set()
                async for response in response_stream:
                    for rel in response.relationships:
                        subj = rel.subject.object
                        if (
                            subj.object_type == ObjectTypeEnum.DIRECTORY.value
                            and subj.object_id not in (UNDEFINED, None, "")
                        ):
                            parents.add(str(subj.object_id))
                if parents and parents.issubset(sub_directory_ids):
                    exclusive_notes.add(note_id)
            note_ids = exclusive_notes

            exclusive_attachments: set[str] = set()
            for attachment_key in attachment_ids:
                response_stream = self.client.ExportBulkRelationships(
                    ExportBulkRelationshipsRequest(
                        optional_relationship_filter=RelationshipFilter(
                            resource_type=ObjectTypeEnum.ATTACHMENT.value,
                            optional_resource_id=attachment_key,
                            optional_relation=AttachmentRelationEnum.PARENT_NOTE,
                        ),
                        **consistency_kwargs,
                    )
                )
                parents: set[str] = set()
                async for response in response_stream:
                    for rel in response.relationships:
                        subj = rel.subject.object
                        if (
                            subj.object_type == ObjectTypeEnum.NOTE.value
                            and subj.object_id not in (UNDEFINED, None, "")
                        ):
                            parents.add(str(subj.object_id))
                if parents and parents.issubset(note_ids):
                    exclusive_attachments.add(attachment_key)
            attachment_ids = exclusive_attachments

        return ResolvedChildren(
            sub_directory_ids=sorted(sub_directory_ids),
            note_ids=sorted(note_ids),
            attachment_ids=sorted(attachment_ids),
        )


