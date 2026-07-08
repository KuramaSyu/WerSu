"""gRPC adapter for :class:`src.services.user.UserServiceABC`.

Implements ``UserService`` from ``grpc/proto/user.proto``: get /
create / alter / delete users.  Only ``GetUser`` and ``PostUser``
are wired up to the service layer so far; ``AlterUser`` and
``DeleteUser`` are stubs reserved for future work.
"""

from __future__ import annotations

import traceback

import asyncpg
import grpc
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.db.entities.user.user import UserEntity
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.user_pb2 import (
    AlterUserRequest,
    DeleteUserRequest,
    DeleteUserResponse,
    GetUserRequest,
    PostUserRequest,
    User,
)
from src.grpc_mod.proto.user_pb2_grpc import UserServiceServicer
from src.services.user import UserServiceABC


class GrpcUserService(UserServiceServicer):
    """
    Implements the gRPC service defined in grpc/proto/user.proto
    """

    def __init__(
        self,
        user_service: UserServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
    ):
        self.user_service = user_service
        self.log = log(__name__, self)
        self._to_grpc = to_grpc

    @log_service_call()
    async def GetUser(self, request: GetUserRequest, context: ServicerContext) -> User:
        try:
            return await self._GetUser(request, context)
        except Exception:
            self.log.error(f"Error fetching user: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching user")
            return User()

    @log_service_call()
    async def _GetUser(self, request: GetUserRequest, context: ServicerContext) -> User:
        if request.HasField("id"):
            user_entity = await self.user_service.get_user(user_id=request.id)
        elif request.HasField("discord_id"):
            user_entity = await self.user_service.get_user(discord_id=request.discord_id)
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
        return user_entity.convert(self._to_grpc)

    @log_service_call()
    async def AlterUser(self, request: AlterUserRequest, context: ServicerContext) -> User:
        ...

    @log_service_call()
    async def DeleteUser(self, request: DeleteUserRequest, context: ServicerContext) -> DeleteUserResponse:
        ...

    @log_service_call()
    async def PostUser(self, request: PostUserRequest, context: ServicerContext) -> User:
        try:
            user_entity = await self.user_service.create_user(
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
            return user_entity.convert(self._to_grpc)
        except asyncpg.UniqueViolationError:
            context.set_code(grpc.StatusCode.ALREADY_EXISTS)
            context.set_details("User with the given discord_id already exists")
            return User()
        except Exception:
            self.log.error(f"Error creating user: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while creating user")
            return User()