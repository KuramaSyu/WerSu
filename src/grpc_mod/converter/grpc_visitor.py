"""Concrete :class:`EntityVisitor` that converts each supported entity to its gRPC message.

The visitor holds the per-entity conversion logic (previously split across
`note_entity_converter`, `directory_entity_converter`, `user_entity_converter`,
`attachment_converter` and `share_converters`). Callers go through
`entity.visit(visitor)` rather than reaching for a free function.

Each `visit_*` method is intentionally a complete conversion -- it does
not delegate out of this module.  Keeping the logic here is what lets the
`src/grpc_mod/converter/` directory host just one file: this one.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from google.protobuf.timestamp_pb2 import Timestamp

from src.api import NoteResponse
from src.api.other.relationship import ObjectTypeEnum
from src.api.other.undefined import UNDEFINED, UndefinedOr, is_undefined, unwrap_undefined
from src.api.other.user_context import ActorAs
from src.db.entities.activity import ActivityEntity, ActivityScore
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.sharing import NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.api.other.visitor import EntityVisitor
from src.db.repos.attachments.attachments import Attachment
from src.grpc_mod.proto.activity_pb2 import (
    ACCESSED_AS_SYSTEM,
    ACCESSED_AS_UNSPECIFIED,
    ACCESSED_AS_USER,
    AccessedAs,
    Activity,
    ActivityScore as GrpcActivityScore,
)
from src.grpc_mod.proto.attachments_pb2 import Attachment as GrpcAttachment
from src.grpc_mod.proto.attachments_pb2 import AttachmentMetadata
from src.grpc_mod.proto.note_pb2 import (
    Directory,
    MinimalDirectory,
    MinimalNote,
    MinimalTag,
    Note,
    NotesReply,
    NoteResponse as GrpcNoteResponse,
    PermissionObjectType,
    PermissionRelationship,
    PermissionResource,
    PermissionSubject,
)
from src.grpc_mod.proto.sharing_pb2 import (
    SHARE_PERMISSION_READ,
    SHARE_PERMISSION_UNSPECIFIED,
    SHARE_PERMISSION_WRITE,
    GetShareUserResponse,
    NoteShare,
    NullableString,
    NullableTimestamp,
    SharePermission,
)
from src.grpc_mod.proto.user_pb2 import User
from src.utils import asdict
from src.utils.dict_helper import drop_except_keys, drop_undefined


def _to_permission_object_type(object_type: str) -> PermissionObjectType.ValueType:
    if object_type == ObjectTypeEnum.NOTE.value:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_NOTE
    if object_type == ObjectTypeEnum.DIRECTORY.value:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_DIRECTORY
    if object_type == ObjectTypeEnum.USER.value:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_USER
    if object_type == ObjectTypeEnum.ATTACHMENT.value:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_ATTACHMENT
    return PermissionObjectType.PERMISSION_OBJECT_TYPE_UNSPECIFIED


def _convert_permissions(permissions: Any) -> list[PermissionRelationship]:
    """Translate each domain :class:`Relationship` into its proto equivalent."""
    converted: list[PermissionRelationship] = []
    for perm in permissions:
        converted.append(
            PermissionRelationship(
                relation=str(perm.relation),
                subject=PermissionSubject(
                    object_type=_to_permission_object_type(str(perm.subject.object_type)),
                    object_id=str(perm.subject.object_id),
                ),
                resource=PermissionResource(
                    object_type=_to_permission_object_type(str(perm.resource.object_type)),
                    object_id=str(perm.resource.object_id),
                ),
            )
        )
    return converted


def _to_proto_nullable_string(value: Any) -> NullableString | None:
    """Convert a domain nullable string into its protobuf wrapper."""
    if is_undefined(value):
        return None
    if value is None:
        return NullableString(null_value=True)
    return NullableString(value=str(value))


def _to_proto_nullable_timestamp(value: Any) -> NullableTimestamp | None:
    """Convert a domain nullable datetime into its protobuf wrapper."""
    if is_undefined(value):
        return None
    if value is None:
        return NullableTimestamp(null_value=True)
    return NullableTimestamp(value=_to_proto_timestamp(value))


def _to_proto_timestamp(value: Any) -> Timestamp | None:
    """Convert a domain datetime into a protobuf Timestamp."""
    if isinstance(value, Timestamp):
        return value
    if not isinstance(value, _dt.datetime):
        return None
    ts = Timestamp()
    ts.FromDatetime(value)
    return ts


def _attachment_timestamp(value: Any) -> Timestamp:
    """Build a proto ``Timestamp`` from an ``UndefinedOr[datetime]`` value."""
    ts = Timestamp()
    if not isinstance(value, _dt.datetime):
        return ts
    try:
        ts.FromDatetime(value)
    except ValueError:
        ts.FromDatetime(_dt.datetime.now())
    return ts


def _domain_permission_to_grpc(permission: Any) -> SharePermission.ValueType:
    """Map a domain ``UndefinedOr[Literal["read", "write"]]`` onto the proto enum."""
    if not permission:
        return SHARE_PERMISSION_UNSPECIFIED
    if permission == "read":
        return SHARE_PERMISSION_READ
    if permission == "write":
        return SHARE_PERMISSION_WRITE
    raise ValueError(f"Invalid permission for a share: {permission}")


class ConvertToGrpcVisitor(EntityVisitor):
    """Convert each supported entity to its gRPC protobuf message.

    The visitor is stateless; instantiate it per dispatch or reuse a
    single instance across many ``visit`` calls.  Inject it into gRPC
    services rather than holding a global instance.

    Implementations:
        * :class:`ConvertToGrpcVisitor` -- this class.
    """

    # ---- notes ---------------------------------------------------------

    def visit_note(self, entity: NoteEntity) -> Note:
        """Convert a :class:`~src.db.entities.note.metadata.NoteEntity` to a ``Note`` message.

        Args:
            entity: The note entity to convert.

        Returns:
            The equivalent gRPC ``Note`` message.
        """
        assert entity.note_id is not None
        assert entity.title is not None
        assert entity.content is not None
        assert entity.author_id is not None

        updated_at_ts = Timestamp()
        if entity.updated_at:
            updated_at_ts.FromDatetime(entity.updated_at)

        basic_args = drop_undefined(
            drop_except_keys(
                asdict(entity),
                {"note_id", "title", "content", "author_id", "permissions"},
            )
        )
        basic_args["id"] = basic_args.pop("note_id")

        # permissions are deprecated
        # assert isinstance(entity.permissions, list)
        # basic_args["permissions"] = _convert_permissions(entity.permissions)
        basic_args["permissions"]: List[PermissionRelationship] = []

        # ``directory_ids`` and ``tag_ids`` land on the proto only when
        # the entity actually populated them -- otherwise drop the
        # UNDEFINED placeholder so the proto field stays at its
        # default (empty list).
        if entity.directory_ids:
            basic_args["directory_ids"] = [
                str(v) for v in entity.directory_ids if v
            ]
        if entity.tag_ids:
            basic_args["tag_ids"] = [
                str(v) for v in entity.tag_ids if v
            ]

        return Note(
            **basic_args,
            updated_at=updated_at_ts,
        )

    def visit_note_minimal(self, entity: NoteEntity) -> MinimalNote:
        """Convert a :class:`~src.db.entities.note.metadata.NoteEntity` to a ``MinimalNote`` message.

        Args:
            entity: The note entity to convert.

        Returns:
            The equivalent gRPC ``MinimalNote`` message (used in search).
        """
        assert entity.note_id is not None
        assert entity.title is not None
        assert entity.content is not None
        assert entity.author_id is not None

        basic_args = drop_undefined(
            drop_except_keys(
                asdict(entity),
                {"note_id", "title", "content", "author_id", "updated_at", "permissions"},
            )
        )

        perms = basic_args.pop("permissions", [])
        basic_args["permissions"] = _convert_permissions(perms)
        basic_args["id"] = basic_args.pop("note_id")
        basic_args["stripped_content"] = basic_args.pop("content")
        if entity.directory_ids:
            basic_args["directory_ids"] = [
                str(v) for v in entity.directory_ids if v
            ]
        if entity.tag_ids:
            basic_args["tag_ids"] = [
                str(v) for v in entity.tag_ids if v
            ]
        return MinimalNote(**basic_args)

    def visit_note_response(self, response: NoteResponse) -> GrpcNoteResponse:
        """Convert a :class:`~src.api.note_service.NoteResponse` to a gRPC ``NoteResponse``."""
        proto_note = (
            self.visit_note(response.note) if response.note else Note()
        )
        return GrpcNoteResponse(
            note=proto_note,
            id_token_map=dict(response.id_token_map),
        )

    # ---- notes reply ---------------------------------------------------

    def visit_notes_reply(
        self,
        notes: list[NoteEntity],
        directories: list[MinimalDirectory] | None = None,
        tags: list[MinimalTag] | None = None,
    ) -> NotesReply:
        """Build a :class:`NotesReply` for the search / directory RPCs.

        Args:
            notes: note entities to convert.
            directories: pre-fetched
                :class:`src.grpc_mod.proto.note_pb2.MinimalDirectory`
                messages -- one per directory referenced anywhere
                in `notes`.  Callers fetch these via
                :meth:`src.db.repos.directory.directory.DirectoryFacadeABC`
                so the visitor stays free of DB access.
            tags: pre-fetched
                :class:`src.grpc_mod.proto.note_pb2.MinimalTag`
                messages -- one per tag referenced anywhere in
                `notes`.

        Returns:
            NotesReply: the proto message with `notes`, `directories`
            and `tags` populated.
        """
        proto_notes = [self.visit_note_minimal(n) for n in notes]
        return NotesReply(
            notes=proto_notes,
            directories=list(directories or []),
            tags=list(tags or []),
        )

    @staticmethod
    def minimal_directory(
        directory_id: str,
        slug: str = "",
        display_name: str = "",
    ) -> MinimalDirectory:
        """Build a single :class:`MinimalDirectory` proto message."""
        return MinimalDirectory(
            id=str(directory_id),
            slug=str(slug or ""),
            display_name=str(display_name or ""),
        )

    @staticmethod
    def minimal_tag(
        tag_id: str,
        slug: str = "",
        display_name: str = "",
    ) -> MinimalTag:
        """Build a single :class:`MinimalTag` proto message."""
        return MinimalTag(
            id=str(tag_id),
            slug=str(slug or ""),
            display_name=str(display_name or ""),
        )

    # ---- directory -----------------------------------------------------

    def visit_directory(self, entity: DirectoryEntity) -> Directory:
        """Convert a :class:`~src.db.entities.directory.directory.DirectoryEntity` to a ``Directory`` message.

        Args:
            entity: The directory entity to convert.

        Returns:
            The equivalent gRPC ``Directory`` message.
        """
        relationships: list[PermissionRelationship] = []
        if isinstance(entity.relations, list):
            relationships = _convert_permissions(entity.relations)

        slug_value = ""
        if entity.slug:
            slug_value = str(entity.slug)
        display_name = ""
        if entity.display_name:
            display_name = str(entity.display_name)
        description = ""
        if entity.description:
            description = str(entity.description)
        image_url = ""
        if entity.image_url:
            image_url = str(entity.image_url)

        kwargs: dict[str, Any] = {
            "id": "" if entity.id in (UNDEFINED, None) else str(entity.id),
            "slug": slug_value,
            "display_name": display_name,
            "description": description,
            "image_url": image_url,
            "relationships": relationships,
        }

        # Multi-parent: emit the full id list when the entity carried
        # one.  An UNDEFINED placeholder becomes the proto default.
        if entity.parent_directory_ids:
            kwargs["parent_dir_ids"] = [
                str(v) for v in entity.parent_directory_ids if v
            ]
        # Optional child lists.
        if entity.child_directory_ids:
            kwargs["child_dir_ids"] = [
                str(v) for v in entity.child_directory_ids if v
            ]
        if entity.child_note_ids:
            kwargs["child_note_ids"] = [
                str(v) for v in entity.child_note_ids if v
            ]

        return Directory(**kwargs)

    # ---- user ----------------------------------------------------------

    def visit_user(self, entity: UserEntity) -> User:
        """Convert a :class:`~src.db.entities.user.user.UserEntity` to a ``User`` message.

        Args:
            entity: The user entity to convert.

        Returns:
            The equivalent gRPC ``User`` message.
        """
        assert entity.id
        assert entity.discord_id
        assert entity.avatar
        assert entity.username
        assert entity.email

        return User(
            id=entity.id,
            discord_id=entity.discord_id,
            avatar=entity.avatar,
            username=entity.username,
            discriminator=entity.discriminator or "",
            email=entity.email,
        )

    # ---- note share ----------------------------------------------------

    def visit_note_share(self, entity: NoteShareEntity) -> NoteShare:
        """Convert a :class:`~src.db.entities.note.sharing.NoteShareEntity` to a ``NoteShare`` message.

        Args:
            entity: The share entity to convert.

        Returns:
            The equivalent gRPC ``NoteShare`` message.
        """
        return NoteShare(
            id=unwrap_undefined(entity.id),
            description=_to_proto_nullable_string(entity.description),
            note_id=unwrap_undefined(entity.note_id),
            created_at=_to_proto_timestamp(entity.created_at),
            created_by=unwrap_undefined(entity.created_by),
            online_since=_to_proto_nullable_timestamp(entity.online_since),
            online_until=_to_proto_nullable_timestamp(entity.online_until),
            access_as=unwrap_undefined(entity.access_as),
            permission=_domain_permission_to_grpc(entity.permission),
        )

    # ---- attachment ----------------------------------------------------

    def visit_attachment(self, entity: Attachment) -> GrpcAttachment:
        """Convert an :class:`~src.db.repos.attachments.attachments.Attachment` to an ``Attachment`` message.

        Args:
            entity: The attachment entity to convert.

        Returns:
            The equivalent gRPC ``Attachment`` message (metadata + content).
        """
        return GrpcAttachment(
            metadata=self._attachment_metadata(entity),
            content=entity.content or bytes(),
        )

    def visit_attachment_metadata(self, entity: Attachment) -> AttachmentMetadata:
        """Convert an :class:`~src.db.repos.attachments.attachments.Attachment` to an ``AttachmentMetadata`` message.

        Args:
            entity: The attachment entity to convert.

        Returns:
            The equivalent gRPC ``AttachmentMetadata`` message.
        """
        if not entity.key:
            return AttachmentMetadata()

        return AttachmentMetadata(
            key=str(entity.key),
            filename=entity.filename or "",
            filepath=entity.filepath or "",
            content_type=entity.content_type or "",
            size=entity.size or 0,
            created_at=_attachment_timestamp(entity.created_at),
            updated_at=_attachment_timestamp(entity.updated_at),
            sha256=entity.sha256,
        )

    def _attachment_metadata(self, entity: Attachment) -> AttachmentMetadata:
        return self.visit_attachment_metadata(entity)

    # ---- share-user tuple ----------------------------------------------

    def visit_share_user(
        self,
        access_as: str,
        online_until: Any,
    ) -> "GetShareUserResponse":
        """Build a ``GetShareUserResponse`` from its raw share-user fields.

        The ``online_until`` field follows the share semantics already
        established for shares: :obj:`~src.api.undefined.UNDEFINED` means the
        share does not advertise an expiry, :data:`None` explicitly means
        "never expires", and a concrete timestamp is forwarded as a
        ``NullableTimestamp``.
        """
        return GetShareUserResponse(
            access_as=access_as,
            online_until=_to_proto_nullable_timestamp(online_until),
        )

    # ---- activity log --------------------------------------------------

    def visit_activity(self, entity: ActivityEntity) -> Activity:
        """Convert an :class:`~src.db.entities.activity.ActivityEntity` to a gRPC ``Activity`` message.

        ``metadata`` is JSON-serialised into ``metadata_json`` because
        proto messages don't model nested maps-of-anything cleanly.
        The ``note_title`` / ``note_stripped_content`` enrichment that
        the activity statistics service stamps onto history rows is
        folded into that same JSON payload so callers see them as
        regular metadata keys without new proto fields.
        """
        assert isinstance(entity, ActivityEntity)
        assert entity.id

        accessed_as_value = self._accessed_as_to_proto(entity.accessed_as)
        at_ts = Timestamp()
        if isinstance(entity.at, _dt.datetime):
            at_ts.FromDatetime(entity.at)

        basic_args = drop_undefined(
            drop_except_keys(
                asdict(entity),
                {"id", "actor_id", "accessed_as", "action",
                 "note_id", "directory_id", "role_id", "at", "metadata",
                 "note_title", "note_stripped_content"},
            )
        )
        # strip the per-row flags we model separately
        basic_args.pop("accessed_as", None)
        basic_args.pop("at", None)
        basic_args.pop("metadata", None)
        basic_args.pop("note_title", None)
        basic_args.pop("note_stripped_content", None)

        return Activity(
            id=basic_args.pop("id"),
            actor_id=basic_args.pop("actor_id", "") or "",
            accessed_as=accessed_as_value,
            action=basic_args.pop("action", "") or "",
            note_id=basic_args.pop("note_id", "") or "",
            directory_id=basic_args.pop("directory_id", "") or "",
            role_id=basic_args.pop("role_id", "") or "",
            at=at_ts,
            metadata_json=self._build_activity_metadata_json(entity),
        )

    def visit_activity_score(self, score: ActivityScore) -> GrpcActivityScore:
        """Convert an :class:`~src.db.entities.activity.ActivityScore` to a gRPC ``ActivityScore``.

        ``title`` and ``stripped_content`` are forwarded as direct
        proto fields so callers can render previews without parsing
        JSON.  Both default to the empty string when the service
        layer did not enrich the row (e.g. a deleted note).
        """
        assert isinstance(score, ActivityScore)
        title = score.title if not is_undefined(score.title) else ""
        stripped = (
            score.stripped_content
            if not is_undefined(score.stripped_content)
            else ""
        )
        return GrpcActivityScore(
            note_id=score.note_id,
            score=float(score.score),
            title=str(title or ""),
            stripped_content=str(stripped or ""),
        )

    @staticmethod
    def _accessed_as_to_proto(accessed_as: UndefinedOr[ActorAs]) -> AccessedAs.ValueType:
        """Translate the ``accessed_as`` literal into the proto enum int."""
        if accessed_as == "system":
            return ACCESSED_AS_SYSTEM
        if accessed_as == "user":
            return ACCESSED_AS_USER
        return ACCESSED_AS_UNSPECIFIED

    @staticmethod
    def _metadata_to_json(metadata: Any) -> str:
        """Serialise the per-row metadata payload to a JSON string.

        ``UNDEFINED`` and ``None`` both produce an empty object so the
        client always sees valid JSON.
        """
        if is_undefined(metadata) or metadata is None:
            return "{}"
        try:
            return json.dumps(dict(metadata))
        except (TypeError, ValueError):
            return json.dumps({})

    @classmethod
    def _build_activity_metadata_json(cls, entity: ActivityEntity) -> str:
        """Serialise the activity row's metadata, plus enrichment fields.

        The activity statistics service stamps ``note_title`` /
        ``note_stripped_content`` onto rows when the query is pinned
        to a single note.  Those ride on the existing
        ``metadata_json`` payload so we don't have to add new proto
        fields to ``Activity``; this helper merges them on top of the
        user-supplied metadata without ever clobbering keys the
        caller actually wrote.
        """
        merged: dict[str, Any] = {}
        raw = cls._metadata_to_json(entity.metadata)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    merged.update(parsed)  # type: ignore
            except (TypeError, ValueError):
                # Fall through; existing metadata will be dropped.
                pass
        if not is_undefined(entity.note_title) and entity.note_title:
            merged["note_title"] = entity.note_title
        if (
            not is_undefined(entity.note_stripped_content)
            and entity.note_stripped_content
        ):
            merged["note_stripped_content"] = entity.note_stripped_content
        try:
            return json.dumps(merged)
        except (TypeError, ValueError):
            return json.dumps({})