from datetime import datetime
import traceback
import grpc
from grpc.aio import ServicerContext

from db.repos import NoteRepoABC
from db.entities import NoteEntity
from grpc_mod import (
    GetNoteRequest, NoteEmbedding, 
    NotePermission, PostNoteRequest, Note,
    NoteService, NoteServiceServicer,
    UserServiceServicer, GetUserRequest, User, 
    AlterUserRequest, DeleteUserRequest, 
    DeleteUserResponse, PostUserRequest
)
from grpc_mod.converter import to_grpc_note, to_grpc_user
from db import UserRepoABC, UserEntity


class GRPCNoteService(NoteServiceServicer):
    """
    Implements the gRPC service defined in grpc/proto/note.proto
    """

    def __init__(self, repo: NoteRepoABC):
        self.repo = repo

    async def GetNote(self, request: GetNoteRequest, context: ServicerContext) -> Note:
        note_entity = await self.repo.select(note=NoteEntity(
            note_id=request.id,
            author_id=None,
            content=None,
            embeddings=[],
            permissions=[],
            title=None,
            updated_at=None
        ))
        assert (note_entity 
            and note_entity.note_id 
            and note_entity.author_id 
            and note_entity.content 
            and note_entity.title
        )

        # conversion from note_entity to gRPC Note Message
        return Note(
            id=note_entity.note_id,
            title=note_entity.title,
            author_id=note_entity.author_id,
            content=note_entity.content,
            embeddings=[
                NoteEmbedding(
                    model=e.model,
                    embedding=e.embedding,
                ) for e in note_entity.embeddings
            ],
            permissions=[
                NotePermission(
                    role_id=p.role_id
                ) for p in note_entity.permissions
            ]

        )

    async def PostNote(self, request: PostNoteRequest, context: ServicerContext) -> Note:
        note_entity = await self.repo.insert(
            NoteEntity(
                note_id=None,
                author_id=request.author_id,
                content=request.content,
                embeddings=[],
                permissions=[],
                title=request.title,
                updated_at=datetime.now(),
            )
        )
        return to_grpc_note(note_entity)

class GRPCUserService(UserServiceServicer):
    """
    Implements the gRPC service defined in grpc/proto/user.proto
    """

    def __init__(self, user_repo: UserRepoABC):
        self.repo = user_repo

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
            print(f"Created user entity: {user_entity}")
            return to_grpc_user(user_entity)
        except Exception as e:
            traceback.print_exc()