import logging
from logging import getLogger, basicConfig
import sys
import os
import time

import asyncio
from typing import Optional, Callable
import boto3
from authzed.api.v1 import AsyncClient
import grpc

from src.api.sharing import ShareAccessServiceABC, SharingRepoABC
from src.api.undefined import UNDEFINED, UndefinedOr, UndefinedType
from src.db.repos.directory.directory import DirectoryRepoSpicedbPostgres
from src.db.migrations.context import MigrationContext
from src.db.migrations.runner import MigrationRunner
from src.db.repos import NotePermissionRepoSpicedb
from src.db.repos.sharing.sharing import SharingPostgresRepo
from src.grpc_mod.proto.sharing_pb2_grpc import add_SharingServiceServicer_to_server
from src.grpc_mod.sharing_service import GrpcSharingService
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.services import PermissionServiceRepo, UserService, DirectoryActivityService, AttachmentFacade, share_access
from src.services.sharing import DefaultSharingService
from src.facades.share_action_facade import ShareActionFacade
from src.utils import logging_provider
from src.db import Database, NoteEmbeddingPostgresRepo, NoteVersionPostgresRepo
from src.db.repos.attachments.attachments import (
    AttachmentsMetadataPostgresRepo,
    AttachmentsMetadataRepoABC,
    AttachmentsRepoABC,
    AttachmentsS3Repo,
)
from src.db.repos.user.user import UserPostgresRepo
from src.db.repos.user.user_action import UserActionPostgresRepo
from src.db.table import Table, setup_table_logging
from src.grpc_mod.proto.attachments_pb2_grpc import add_AttachmentServiceServicer_to_server
from src.grpc_mod.proto.note_pb2_grpc import (
    add_DirectoryServiceServicer_to_server,
    add_NoteServiceServicer_to_server,
    add_PermissionServiceServicer_to_server,
    add_NoteVersionServiceServicer_to_server,
)
from src.grpc_mod.proto.user_pb2_grpc import add_UserServiceServicer_to_server
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.note import NoteRepoFacade
from src.grpc_mod.service import (
    GrpcAttachmentService,
    GrpcDirectoryService,
    GrpcNoteService,
    GrpcNoteVersionService,
    GrpcPermissionService,
    GrpcUserService,
)
from src.ai.embedding_generator import EmbeddingGenerator, Models
from src.utils.spicedb_client import create_spicedb_async_client



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
    """
    Construction Root which instantiates all dependencies, wires them 
    together via constructor injection, and starts the gRPC server with 
    the most upper layer service implementations.  This is the entrypoint for the application.
    """
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
    max_note_deltas = int(get_os_env_variable("NOTE_VERSION_MAX_DELTAS", log, "10"))
    s3_endpoint = get_os_env_variable("S3_ENDPOINT", log, "http://localhost:3900")
    s3_region = get_os_env_variable("S3_REGION", log, "garage")
    s3_access_key = get_os_env_variable("GARAGE_DEFAULT_ACCESS_KEY", log, UNDEFINED)
    s3_secret_key = get_os_env_variable("GARAGE_DEFAULT_SECRET_KEY",log, UNDEFINED)
    s3_bucket = get_os_env_variable("GARAGE_DEFAULT_BUCKET", log, UNDEFINED)
    

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
    spicedb_client = create_spicedb_async_client(
        target=grpc_spicedb_address,
        bearer_token=grpc_spicedb_credentials,
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
    version_snapshot_table = Table(
        **common_table_kwargs,
        table_name="note.version_snapshot",
        id_fields=["snapshot_id"],
    )
    version_delta_table = Table(
        **common_table_kwargs,
        table_name="note.version_delta",
        id_fields=["delta_id"],
    )
    attachments_table = Table(
        **common_table_kwargs,
        table_name="note.attachment",
        id_fields=["key"],
    )
    attachments_note_link_table = Table(
        **common_table_kwargs,
        table_name="note.attachment_note_link",
        id_fields=["note_id", "attachment_key"],
    )

    shared_table = Table(
        **common_table_kwargs,
        table_name="shared",
        id_fields=["id"],
    )
    user_action_table = Table(
        **common_table_kwargs,
        table_name="user_action",
        id_fields=["id"],
    )

    # setup S3 connection
    s3_client = boto3.client(
        "s3",
        endpoint_url=s3_endpoint,
        aws_access_key_id=s3_access_key,
        aws_secret_access_key=s3_secret_key,
        region_name=s3_region,
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

    ### Setup Repos ###
    user_repo = UserPostgresRepo(db=db)
    permission_repo = NotePermissionRepoSpicedb(client=spicedb_client, consistent=True)

    directory_repo = DirectoryRepoSpicedbPostgres(
        db=db,
        permission_repo=permission_repo,
        spicedb_client=spicedb_client,
    )

    version_repo = NoteVersionPostgresRepo(
        snapshot_table=version_snapshot_table,
        delta_table=version_delta_table,
        max_deltas_per_snapshot=max_note_deltas,
    )

    note_repo: NoteRepoFacade = NoteRepoFacade(
        db=db,
        content_repo=NoteContentPostgresRepo(content_table),
        embedding_repo=NoteEmbeddingPostgresRepo(
            table=embedding_table,
            embedding_generator=embedding_generator
        ),
        permission_repo=permission_repo,
        directory_repo=directory_repo,
        logging_provider=logging_provider,
        version_repo=version_repo,
    )
    attachments_repo: AttachmentsRepoABC = AttachmentsS3Repo(
        client=s3_client,  # type:ignore
        bucket=s3_bucket,
    )
    metadata_repo: AttachmentsMetadataRepoABC = AttachmentsMetadataPostgresRepo(
        table=attachments_table
    )
    sharing_repo: SharingRepoABC = SharingPostgresRepo(
        table=shared_table,
        logging_provider=logging_provider,
    )
    user_action_repo = UserActionPostgresRepo(
        table=user_action_table,
        logging_provider=logging_provider,
    )

    ### Setup services and inject repos ###
    attachment_service = AttachmentFacade(
        attachment_repo=attachments_repo,
        metadata_repo=metadata_repo,
        permission_repo=permission_repo,
        attachments_note_link_table=attachments_note_link_table,
        log=logging_provider,
    )

    permission_service = PermissionServiceRepo(
        permission_repo=permission_repo,
        note_repo=note_repo,
        directory_repo=directory_repo,
    )

    directory_activity_service = DirectoryActivityService(
        version_repo=version_repo,
        directory_repo=directory_repo,
        log=logging_provider,
    )
    note_version_service = GrpcNoteVersionService(
        note_repo=note_repo,
        version_repo=version_repo,
        directory_activity_service=directory_activity_service,
        log=logging_provider,
        to_grpc=grpc_visitor,
    )
    sharing_service = DefaultSharingService(
        share_facade=ShareActionFacade(
            sharing_repo=sharing_repo,
            user_repo=user_repo,
            user_action_repo=user_action_repo,
            logging_provider=logging_provider,
        ),
        permission_repo=permission_repo,
        permission_service=permission_service,
        logging_provider=logging_provider,
    )
    share_access_service: ShareAccessServiceABC = share_access.ShareAccessService(
        sharing_repo=sharing_repo,
        permission_repo=permission_repo,
        user_repo=user_repo,
        user_action_repo=user_action_repo,
        logger=logging_provider,
    )

    ### Register gRPC services by injecting the service layer ###
    log.info("Setting up gRPC services...")
    grpc_visitor = ConvertToGrpcVisitor()
    note_service = GrpcNoteService(repo=note_repo, log=logging_provider, to_grpc=grpc_visitor)
    add_NoteServiceServicer_to_server(note_service, server)


    add_NoteVersionServiceServicer_to_server(note_version_service, server)

    directory_service = GrpcDirectoryService(
        directory_repo=directory_repo,
        log=logging_provider,
        to_grpc=grpc_visitor,
    )
    add_DirectoryServiceServicer_to_server(directory_service, server)


    grpc_permission_service = GrpcPermissionService(
        permission_service=permission_service,
        log=logging_provider,
        to_grpc=grpc_visitor,
    )
    add_PermissionServiceServicer_to_server(grpc_permission_service, server)

    grpc_sharing_service = GrpcSharingService(
        sharing_service=sharing_service,
        share_access_service=share_access_service,
        log=logging_provider,
        to_grpc=grpc_visitor,
    )
    add_SharingServiceServicer_to_server(grpc_sharing_service, server)

    # setup gRPC user service
    app_user_service = UserService(user_repo=user_repo, directory_repo=directory_repo)
    grpc_user_service = GrpcUserService(user_service=app_user_service, log=logging_provider, to_grpc=grpc_visitor)
    add_UserServiceServicer_to_server(grpc_user_service, server)

    # setup gRPC attachment service
    grpc_attachment_service = GrpcAttachmentService(
        attachment_service=attachment_service,
        log=logging_provider,
        to_grpc=grpc_visitor,
    )
    add_AttachmentServiceServicer_to_server(grpc_attachment_service, server)

    # configure server
    listen_addr = f"{grpc_host}:{grpc_port}"
    server.add_insecure_port(listen_addr)
    log.info(f"gRPC server listening on {listen_addr}")

    # Start the server
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())