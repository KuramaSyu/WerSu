from datetime import datetime
import traceback
from logging import getLogger
import logging
from typing import AsyncIterator, Callable, List, Optional

import grpc
from grpc.aio import ServicerContext
import asyncpg

from src.api import LoggingProvider
from src.api.types import Pagination
from src.api.undefined import UNDEFINED
from src.db.repos import NoteRepoFacadeABC
from src.db.entities import NoteEntity
from src.db.repos.note.note import SearchType, UserContext
from src.grpc_mod import (
    GetNoteRequest, NoteEmbedding, 
    NotePermission, PostNoteRequest, Note,
    NoteService, NoteServiceServicer,
    UserServiceServicer, GetUserRequest, User, 
    AlterUserRequest, DeleteUserRequest, 
    DeleteUserResponse, PostUserRequest,
)
from src.grpc_mod.converter import to_grpc_note, to_grpc_user
from src.db import UserRepoABC, UserEntity
from src.grpc_mod.converter.note_entity_converter import to_grpc_minimal_note, to_search_type
from src.grpc_mod.proto.note_pb2 import AlterNoteRequest, DeleteNoteRequest, GetSearchNotesRequest, MinimalNote


class GrpcNoteService(NoteServiceServicer):
    """
    Implements the gRPC service defined in grpc/proto/note.proto
    """

    def __init__(self, repo: NoteRepoFacadeABC, log: LoggingProvider):
        self.repo = repo
        self.log = log(__name__, self)
 
    async def GetNote(self, request: GetNoteRequest, context: ServicerContext) -> Note:
        try:
            note_entity = await self.repo.select_by_id(request.id, UserContext(user_id=request.user_id))
            return to_grpc_note(note_entity)
        except Exception:
            self.log.error(f"Error fetching note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching note")
            return Note()

    async def PostNote(self, request: PostNoteRequest, context: ServicerContext) -> Note:
        try:
            note_entity = await self.repo.insert(
                NoteEntity(
                    note_id=UNDEFINED,
                    author_id=request.author_id,
                    content=request.content,
                    embeddings=[],
                    permissions=[],
                    title=request.title,
                    updated_at=datetime.now(),
                )
            )
            return to_grpc_note(note_entity)
        except asyncpg.UniqueViolationError as e:
            context.set_code(grpc.StatusCode.ALREADY_EXISTS)
            context.set_details(f"Insertion error: {e}")
            return Note()
        except Exception:
            self.log.error(f"Error creating note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while creating note")
            return Note()

    async def PatchNote(self, request: AlterNoteRequest, context: ServicerContext) -> Note:
        try:
            note_entity = await self.repo.update(
                NoteEntity(
                    note_id=request.id,
                    author_id=request.author_id,
                    content=request.content,
                    embeddings=UNDEFINED,
                    permissions=UNDEFINED,
                    title=request.title,
                    updated_at=datetime.now(),
                ),
                UserContext(user_id=request.author_id)
            )
            self.log.debug(f"Updated note entity: {note_entity}")
            return to_grpc_note(note_entity)
        except Exception:
            self.log.error(f"Error updating note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while updating note")
            return Note()

    async def DeleteNote(self, request: DeleteNoteRequest, context: ServicerContext) -> Note:
        try:
            deleted_note_entities = await self.repo.delete(
                request.id,
                UserContext(user_id=request.author_id)
            )
            
            if deleted_note_entities is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Note not found where user with id {request.author_id} has permissions")
                return Note()
            assert len(deleted_note_entities) <= 1
            return to_grpc_note(deleted_note_entities[0])
        except Exception:
            self.log.error(f"Error deleting note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while deleting note")
            return Note()
        
    async def SearchNotes(
        self, request: GetSearchNotesRequest, context: ServicerContext
    ) -> AsyncIterator[MinimalNote]:
        notes = await self.repo.search_notes(
            to_search_type(request.search_type),
            request.query,
            pagination=Pagination(limit=request.limit, offset=request.offset),
            ctx=UserContext(user_id=request.user_id),
        )
        for note in notes:
            yield to_grpc_minimal_note(note)


class GrpcUserService(UserServiceServicer):
    """
    Implements the gRPC service defined in grpc/proto/user.proto
    """

    def __init__(self, user_repo: UserRepoABC, log: LoggingProvider):
        self.repo = user_repo
        self.log = log(__name__, self)

    async def GetUser(self, request: GetUserRequest, context: ServicerContext) -> User:
        if request.HasField("id"):
            user_entity = await self.repo.select(user_id=request.id)
        elif request.HasField("discord_id"):
            user_entity = await self.repo.select_by_discord_id(discord_id=request.discord_id)
        else:
            # Neither id nor discord_id provided
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Either 'id' or 'discord_id' must be provided")
            return User()

        if user_entity is None:
            # User not found
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("User not found")
            return User()
        
        # user found and converted to gRPC User Message
        return to_grpc_user(user_entity)

    async def AlterUser(self, request: AlterUserRequest, context: ServicerContext) -> User:
        ...
    
    async def DeleteUser(self, request: DeleteUserRequest, context: ServicerContext) -> DeleteUserResponse:
        ...
    
    async def PostUser(self, request: PostUserRequest, context: ServicerContext) -> User:
        try:
            user_entity = await self.repo.insert(
                UserEntity(
                    id=None,
                    discord_id=request.discord_id,
                    avatar=request.avatar,
                    username=request.username,
                    discriminator=request.discriminator,
                    email=request.email,
                )
            )
            self.log.debug(f"Created user entity: {user_entity}")
            return to_grpc_user(user_entity)
        except asyncpg.UniqueViolationError:
            context.set_code(grpc.StatusCode.ALREADY_EXISTS)
            context.set_details("User with the given discord_id already exists")
            return User()
        except Exception:
            self.log.error(f"Error creating user: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while creating user")
            return User()
