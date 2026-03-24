import logging
from logging import getLogger, basicConfig
import sys
import os
import time

import asyncio
from typing import Optional, Callable
from authzed.api.v1 import AsyncClient
import grpc
from colorama import Fore, Style, init
from grpcutil import insecure_bearer_token_credentials

from src.api.undefined import UNDEFINED, UndefinedOr, UndefinedType
from src.db.repos.directory.directory import DirectoryRepoSpicedbPostgres
from src.db.migrations.context import MigrationContext
from src.db.migrations.runner import MigrationRunner
from src.db.repos.note.permission import NotePermissionRepoSpicedb
from src.services.roles import PermissionServiceRepo
from src.utils import logging_provider
from src.db.database import Database
from src.db.repos.note.embedding import NoteEmbeddingPostgresRepo
from src.db.repos.user.user import UserRepoABC, UserPostgresRepo
from src.db.table import Table, setup_table_logging
from src.grpc_mod.proto.note_pb2_grpc import add_NoteServiceServicer_to_server, add_PermissionServiceServicer_to_server
from src.grpc_mod.proto.user_pb2_grpc import add_UserServiceServicer_to_server
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.note import NoteRepoFacade
from src.grpc_mod.service import GrpcNoteService, GrpcPermissionService, GrpcUserService
from src.ai.embedding_generator import EmbeddingGenerator, Models



def get_os_env_variable(name: str, log: logging.Logger, default: UndefinedOr[str]) -> str:
    """if UNDEFINED, then log ciritcal error and exit"""

    value = os.getenv(name)
    if value is not None:
        return value

    if not isinstance(default, UndefinedType):
        return default

    log.critical(f"{name} environment variable not set and no default provided")
    sys.exit(1)


async def serve():
    # setup logging
    log = logging_provider(__name__)
    setup_table_logging(logging_provider)

    db_dsn = get_os_env_variable(
        "DATABASE_DSN",
        log,
        "postgres://postgres:postgres@localhost:5433/db?sslmode=disable"
    )
    grpc_host = get_os_env_variable("GRPC_HOST", log, "[::]")
    grpc_port = get_os_env_variable("GRPC_PORT", log, "50052")
    grpc_spicedb_credentials = get_os_env_variable("GRPC_SPICEDB_CREDENTIALS", log, UNDEFINED)
    grpc_spicedb_address = get_os_env_variable("GRPC_SPICEDB_ADDRESS", log, UNDEFINED)
    

    # create server 
    server = grpc.aio.server()

    # connect to database
    log.info("Connecting to database...")
    db = Database(
        dsn=db_dsn,
        log=logging_provider
    )
    await db.init_db()

    # connect to spicedb permission service
    log.info("Connecting to SpiceDB permission service...")
    spicedb_client = AsyncClient(
        grpc_spicedb_address,
        insecure_bearer_token_credentials(grpc_spicedb_credentials)
    )

    # run migrations with dependency container
    log.info("Running migrations...")
    migration_runner = MigrationRunner(
        ctx=MigrationContext(
            db=db,
            spicedb_client=spicedb_client,
        ),
        log_provider=logging_provider,
    )
    await migration_runner.run_pending_migrations()
    log.info("Migrations completed")

    # setup db tables and their primary keys
    log.info("Setting up database tables...")
    common_table_kwargs = {"db": db, "logging_provider": logging_provider}
    content_table = Table(
        **common_table_kwargs, 
        table_name="note.content", 
        id_fields=["id"]
    )
    embedding_table = Table(
        **common_table_kwargs,
        table_name="note.embedding",
        id_fields=["note_id", "model"]
    )

    # setup note repo via DI
    log.info("Importing repo and service modules...")
    repo_import_started = time.perf_counter()

    log.info(f"Repo/service imports completed in {time.perf_counter() - repo_import_started:.2f}s")

    

    model_init_started = time.perf_counter()
    embedding_generator = EmbeddingGenerator(
        model_name=Models.MINI_LM_L6_V2,
        logging_provider=logging_provider,
    )
    log.info(f"Embedding model initialized in {time.perf_counter() - model_init_started:.2f}s")

    log.info("Setting up NoteRepoFacade, sub repos and embedding generator...")
    permission_repo = NotePermissionRepoSpicedb(client=spicedb_client)
    directory_repo = DirectoryRepoSpicedbPostgres(
        db=db,
        permission_repo=permission_repo,
        spicedb_client=spicedb_client,
    )

    repo: NoteRepoFacade = NoteRepoFacade(
        db=db,
        content_repo=NoteContentPostgresRepo(content_table),
        embedding_repo=NoteEmbeddingPostgresRepo(
            table=embedding_table,
            embedding_generator=embedding_generator
        ),
        permission_repo=permission_repo,
        directory_repo=directory_repo,
        logging_provider=logging_provider,
    )

    # setup gRPC note service
    log.info("Setting up gRPC services...")
    note_service = GrpcNoteService(repo=repo, log=logging_provider)
    add_NoteServiceServicer_to_server(note_service, server)

    permission_service = PermissionServiceRepo(
        permission_repo=permission_repo,
        note_repo=repo,
        directory_repo=directory_repo,
    )
    grpc_permission_service = GrpcPermissionService(
        permission_service=permission_service,
        log=logging_provider,
    )
    add_PermissionServiceServicer_to_server(grpc_permission_service, server)

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