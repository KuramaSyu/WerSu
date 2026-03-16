import logging
from logging import getLogger, basicConfig
import sys
import os

import asyncio
from typing import Optional, Callable
import grpc
from colorama import Fore, Style, init

from src.utils import logging_provider
from src.ai.embedding_generator import EmbeddingGenerator, Models
from src.db import table
from src.db.repos import NoteRepoFacadeABC, NoteRepoFacade
from src.db import Database
from src.db.repos.note.embedding import NoteEmbeddingPostgresRepo
from src.db.repos.note.permission import NotePermissionPostgresRepo
from src.db.repos.user.user import UserRepoABC, UserPostgresRepo
from src.db.table import Table, setup_table_logging
from src.grpc_mod.proto.user_pb2_grpc import add_UserServiceServicer_to_server
from src.grpc_mod import add_NoteServiceServicer_to_server, GrpcNoteService, GrpcUserService
from src.db.repos.note.content import NoteContentPostgresRepo






async def serve():
    # setup logging
    log = logging_provider(__name__)
    setup_table_logging(logging_provider)

    db_dsn = os.getenv(
        "DATABASE_DSN",
        "postgres://postgres:postgres@localhost:5433/db?sslmode=disable",
    )
    grpc_host = os.getenv("GRPC_HOST", "[::]")
    grpc_port = os.getenv("GRPC_PORT", "50052")

    # create server 
    server = grpc.aio.server()

    # connect to database
    log.info("Connecting to database...")
    db = Database(
        dsn=db_dsn,
        log=logging_provider
    )
    await db.init_db()

    # setup db tables and their primary keys
    log.info("Setting up database tables...")
    common_table_kwargs = {"db": db, "logging_provider": logging_provider}
    content_table = Table(
        **common_table_kwargs, 
        table_name="note.content", 
        id_fields=["id"]
    )
    permission_table = Table(
        **common_table_kwargs, 
        table_name="note.permission", 
        id_fields=["note_id", "role_id"]
    )
    embedding_table = Table(
        **common_table_kwargs,
        table_name="note.embedding",
        id_fields=["note_id", "model"]
    )

    # setup note repo via DI
    log.info("Setting up NoteRepoFacade, sub repos and embedding generator...")
    repo: NoteRepoFacade = NoteRepoFacade(
        db=db,
        content_repo=NoteContentPostgresRepo(content_table),
        embedding_repo=NoteEmbeddingPostgresRepo(
            table=embedding_table,
            embedding_generator=EmbeddingGenerator(
                model_name=Models.MINI_LM_L6_V2, 
                logging_provider=logging_provider
            )
        ),
        permission_repo=NotePermissionPostgresRepo(permission_table),
        logging_provider=logging_provider,
    )

    # setup gRPC note service
    log.info("Setting up gRPC services...")
    note_service = GrpcNoteService(repo=repo, log=logging_provider)
    add_NoteServiceServicer_to_server(note_service, server)

    # setup gRPC user service
    user_repo: UserRepoABC = UserPostgresRepo(db=db)
    user_service = GrpcUserService(user_repo=user_repo, log=logging_provider)
    add_UserServiceServicer_to_server(user_service, server)

    # configure server
    listen_addr = f"{grpc_host}:{grpc_port}"
    server.add_insecure_port(listen_addr)
    log.info(f"gRPC server listening on {listen_addr}")

    # Start the server
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())