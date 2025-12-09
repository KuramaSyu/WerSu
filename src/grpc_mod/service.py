from datetime import datetime
import traceback
from logging import getLogger
import logging
from typing import AsyncIterator, Callable, List, Optional

import grpc
from grpc.aio import ServicerContext
import asyncpg

from api import LoggingProvider
from api.types import Pagination
from api.undefined import UNDEFINED
from db.repos import NoteRepoFacadeABC
from db.entities import NoteEntity
from db.repos.note.note import SearchType
from grpc_mod import (
    GetNoteRequest, NoteEmbedding, 
    NotePermission, PostNoteRequest, Note,
    NoteService, NoteServiceServicer,
    UserServiceServicer, GetUserRequest, User, 
    AlterUserRequest, DeleteUserRequest, 
    DeleteUserResponse, PostUserRequest,
)
from grpc_mod.converter import to_grpc_note, to_grpc_user
from db import UserRepoABC, UserEntity
from grpc_mod.converter.note_entity_converter import to_grpc_minimal_note, to_search_type
from grpc_mod.proto.note_pb2 import GetSearchNotesRequest, MinimalNote


class GrpcNoteService(NoteServiceServicer):
    """
    Implements the gRPC service defined in grpc/proto/note.proto
    """

    def __init__(self, repo: NoteRepoFacadeABC, log: LoggingProvider):
        self.repo = repo
        self.log = log(__name__, self)
 
    async def GetNote(self, request: GetNoteRequest, context: ServicerContext) -> Note:
        try:
            note_entity = await self.repo.select(
                NoteEntity(
                    note_id=request.id,
                    author_id=UNDEFINED,
                    content=UNDEFINED,
                    embeddings=[],
                    permissions=[],
                    title=UNDEFINED,
                    updated_at=UNDEFINED
                )
            )
            assert (note_entity 
                and note_entity.note_id 
                and note_entity.author_id 
                and note_entity.content 
                and note_entity.title
            )

            # conversion from note_entity to gRPC Note Message
            assert all([e.model != UNDEFINED for e in note_entity.embeddings])
            return Note(
                id=note_entity.note_id,
                title=note_entity.title,
                author_id=note_entity.author_id,
                content=note_entity.content,
                embeddings=[
                    NoteEmbedding(
                        model=e.model,  # type: ignore
                        embedding=e.embedding,  # type: ignore
                    ) for e in note_entity.embeddings
                    if e.model != UNDEFINED and e.embedding != UNDEFINED
                ],
                permissions=[
                    NotePermission(
                        role_id=p.role_id  # type: ignore
                    ) for p in note_entity.permissions
                    if p.role_id != UNDEFINED
                ]

            )
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

    async def SearchNotes(
        self, request: GetSearchNotesRequest, context: ServicerContext
    ) -> AsyncIterator[MinimalNote]:
        notes = await self.repo.search_notes(
            to_search_type(request.search_type),
            request.query,
            pagination=Pagination(limit=request.limit, offset=request.offset)
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
                    avatar_url=request.avatar_url,
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
