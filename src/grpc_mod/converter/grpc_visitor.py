"""Concrete :class:`EntityVisitor` that converts each supported entity to its gRPC message.

The visitor delegates to the existing ``to_grpc_*`` converter functions
in this package so the conversion logic stays in one place per entity.
Each ``visit_*`` method forwards to the matching free function and
returns its result.
"""

from __future__ import annotations

from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.sharing import NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.db.entities.visitor import EntityVisitor
from src.db.repos.attachments.attachments import Attachment
from src.grpc_mod.converter.attachment_converter import to_grpc_attachment
from src.grpc_mod.converter.directory_entity_converter import to_grpc_directory
from src.grpc_mod.converter.note_entity_converter import to_grpc_note
from src.grpc_mod.converter.share_converters import to_grpc_note_share
from src.grpc_mod.converter.user_entity_converter import to_grpc_user
from src.grpc_mod.proto.attachments_pb2 import Attachment as GrpcAttachment
from src.grpc_mod.proto.note_pb2 import Directory, Note
from src.grpc_mod.proto.sharing_pb2 import NoteShare
from src.grpc_mod.proto.user_pb2 import User


class ConvertToGrpcVisitor(EntityVisitor):
    """Convert each supported entity to its gRPC protobuf message.

    The visitor is stateless; instantiate it per dispatch or reuse a
    single instance across many ``visit`` calls.

    Implementations:
        * :class:`ConvertToGrpcVisitor` -- this class.
    """

    def visit_note(self, entity: NoteEntity) -> Note:
        """Convert a :class:`~src.db.entities.note.metadata.NoteEntity` to a ``Note`` message.

        Args:
            entity: The note entity to convert.

        Returns:
            The equivalent gRPC ``Note`` message.
        """
        return to_grpc_note(entity)

    def visit_directory(self, entity: DirectoryEntity) -> Directory:
        """Convert a :class:`~src.db.entities.directory.directory.DirectoryEntity` to a ``Directory`` message.

        Args:
            entity: The directory entity to convert.

        Returns:
            The equivalent gRPC ``Directory`` message.
        """
        return to_grpc_directory(entity)

    def visit_user(self, entity: UserEntity) -> User:
        """Convert a :class:`~src.db.entities.user.user.UserEntity` to a ``User`` message.

        Args:
            entity: The user entity to convert.

        Returns:
            The equivalent gRPC ``User`` message.
        """
        return to_grpc_user(entity)

    def visit_note_share(self, entity: NoteShareEntity) -> NoteShare:
        """Convert a :class:`~src.db.entities.note.sharing.NoteShareEntity` to a ``NoteShare`` message.

        Args:
            entity: The share entity to convert.

        Returns:
            The equivalent gRPC ``NoteShare`` message.
        """
        return to_grpc_note_share(entity)

    def visit_attachment(self, entity: Attachment) -> GrpcAttachment:
        """Convert an :class:`~src.db.repos.attachments.attachments.Attachment` to an ``Attachment`` message.

        Args:
            entity: The attachment entity to convert.

        Returns:
            The equivalent gRPC ``Attachment`` message (metadata + content).
        """
        return to_grpc_attachment(entity)