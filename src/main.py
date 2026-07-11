import logging
from logging import getLogger, basicConfig
import sys
import os
import time

import asyncio
from typing import Any, Callable, Dict, Optional, cast
import boto3
from authzed.api.v1 import AsyncClient
import grpc

from src.api.activity_statistics_service import ActivityStatisticsServiceABC
from src.api.jwt_provider import PyJwtProvider
from src.api.sharing import ShareAccessServiceABC, SharingRepoABC
from src.api.undefined import UNDEFINED, UndefinedOr, UndefinedType
from src.db.repos.directory.directory import DirectoryRepoSpicedbPostgres
from src.db.migrations.context import MigrationContext
from src.db.migrations.runner import MigrationRunner
from src.db.repos import NotePermissionRepoSpicedb
from src.db.repos.sharing.sharing import SharingPostgresRepo
from src.grpc_mod.activity_statistics_service import GrpcActivityStatisticsService
from src.grpc_mod.proto.activity_pb2_grpc import add_ActivityStatisticsServiceServicer_to_server
from src.grpc_mod.proto.sharing_pb2_grpc import add_SharingServiceServicer_to_server
from src.grpc_mod.sharing_service import GrpcSharingService
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.services import PermissionServiceRepo, UserService, DirectoryActivityService, AttachmentFacade, share_access
from src.services.activity_logger_service import DefaultActivityLoggerService
from src.services.activity_statistics_service import DefaultActivityStatisticsService
from src.services.sharing import DefaultSharingService
from src.services.note import NoteService
from src.services.directory import DirectoryService
from src.services.thirdparty_migrations import (
    ThirdpartyMigrationsServiceABC,
)
from src.services.thirdparty_migrations.bookstack import BookstackBookImport
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
from src.db.repos.user import RepoContextFactory
from src.db.repos.user.user_action import UserActionPostgresRepo
from src.db.repos.activity.postgres import PostgresActivityRepo
from src.db.table import Table, setup_table_logging
from src.grpc_mod.proto.attachments_pb2_grpc import add_AttachmentServiceServicer_to_server  # type: ignore[attr-defined]
from src.grpc_mod.proto.note_pb2_grpc import (
    add_DirectoryServiceServicer_to_server,  # type: ignore[attr-defined]
    add_NoteServiceServicer_to_server,  # type: ignore[attr-defined]
    add_PermissionServiceServicer_to_server,  # type: ignore[attr-defined]
    add_NoteVersionServiceServicer_to_server,  # type: ignore[attr-defined]
)
from src.grpc_mod.proto.thirdparty_migrations_pb2_grpc import (
    add_ThirdpartyMigrationsServiceServicer_to_server,  # type: ignore[attr-defined]
)
from src.grpc_mod.proto.user_pb2_grpc import add_UserServiceServicer_to_server  # type: ignore[attr-defined]
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.note import NoteFacade
from src.grpc_mod.attachment_service import GrpcAttachmentService
from src.grpc_mod.directory_service import GrpcDirectoryService
from src.grpc_mod.note_service import GrpcNoteService
from src.grpc_mod.note_version_service import GrpcNoteVersionService
from src.grpc_mod.permission_service import GrpcPermissionService
from src.grpc_mod.thirdparty_migrations_service import GrpcThirdpartyMigrationsService
from src.grpc_mod.user_service import GrpcUserService
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
    I decided to not split it into multiple functions, since this shows exactly, how all dependencies are wired together.
    So this is the single point in this application, which shows the complete dependency graph + complexity. 
    """
    # setup logging
    log = logging_provider(__name__)
    setup_table_logging(logging_provider)

    db_dsn = get_os_env_variable(
        "DATABASE_DSN",
        log,
        "postgres://postgres:postgres@localhost:5433/db?sslmode=disable"
    )

    # extract env vars
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
    jwt_secret = get_os_env_variable("JWT_SECRET", log, UNDEFINED)

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
    common_table_kwargs: Dict[str, Any] = {"db": db, "logging_provider": logging_provider}
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

    users_table = Table(
        **common_table_kwargs,
        table_name="users",
        id_fields=["id"],
    )
    activity_table = Table(
        **common_table_kwargs,
        table_name="activity",
        id_fields=["id"],
    )

    # setup S3 connection
    s3_client: Any = boto3.client(  # type: ignore[reportUnknownMemberType]
        "s3",
        endpoint_url=s3_endpoint,
        aws_access_key_id=s3_access_key,
        aws_secret_access_key=s3_secret_key,
        region_name=s3_region,
    )

    # setup note repo via DI
    log.info("Importing repo and service modules...")
    repo_import_started = time.perf_counter()

    # JWT provider for share-link auth tokens.  Same secret as the
    # Go REST API so tokens minted there are accepted here and vice versa.
    log.info("Initialising JWT provider...")
    jwt_provider = PyJwtProvider(secret=jwt_secret)

    log.info(f"Repo/service imports completed in {time.perf_counter() - repo_import_started:.2f}s")

    

    model_init_started = time.perf_counter()
    embedding_generator = EmbeddingGenerator(
        model_name=Models.MINI_LM_L6_V2,
        logging_provider=logging_provider,
    )
    log.info(f"Embedding model initialized in {time.perf_counter() - model_init_started:.2f}s")

    ### Setup Repos ###
    user_repo = UserPostgresRepo(
        table=users_table,
        logging_provider=logging_provider,
    )

    # Factory used by every gRPC service to create user instances
    user_context_factory = RepoContextFactory(user_repo=user_repo)

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

    note_content_repo = NoteContentPostgresRepo(content_table)
    note_repo: NoteFacade = NoteFacade(
        db=db,
        content_repo=note_content_repo,
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
    activity_repo = PostgresActivityRepo(
        table=activity_table,
        directory_repo=directory_repo,
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

    ### Register gRPC services by injecting the service layer ###
    log.info("Setting up gRPC services...")
    grpc_visitor = ConvertToGrpcVisitor()

    note_version_service = GrpcNoteVersionService(
        note_repo=note_repo,
        version_repo=version_repo,
        directory_activity_service=directory_activity_service,
        log=logging_provider,
        to_grpc=grpc_visitor,
        context_factory=user_context_factory,
    )

    activity_logger_service = DefaultActivityLoggerService(
        activity_repo=activity_repo,
        logging_provider=logging_provider,
    )

    app_note_service = NoteService(
        note_repo=note_repo,
        permission_repo=permission_repo,
        jwt_provider=jwt_provider,
        directory_repo=directory_repo,
        activity_logger=activity_logger_service,
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
        user_repo=user_repo,
        activity_logger=activity_logger_service,
    )
    share_access_service: ShareAccessServiceABC = share_access.ShareAccessService(
        sharing_repo=sharing_repo,
        permission_repo=permission_repo,
        user_repo=user_repo,
        user_action_repo=user_action_repo,
        logger=logging_provider,
        context_factory=user_context_factory,
    )
    activity_statistics_service: ActivityStatisticsServiceABC = DefaultActivityStatisticsService(
        activity_repo=activity_repo,
        permission_repo=permission_repo,
        directory_repo=directory_repo,
        note_content_repo=note_content_repo,
        logging_provider=logging_provider,
    )

    grpc_note_service = GrpcNoteService(
        note_service=app_note_service,
        log=logging_provider,
        to_grpc=grpc_visitor,
        context_factory=user_context_factory,
    )
    add_NoteServiceServicer_to_server(grpc_note_service, server)


    add_NoteVersionServiceServicer_to_server(note_version_service, server)

    directory_app_service = DirectoryService(
        directory_repo=directory_repo,
        note_repo=note_repo,
        permission_repo=permission_repo,
        activity_logger=activity_logger_service,
        note_service=app_note_service,
        attachment_facade=attachment_service,
        log=logging_provider,
    )

    migrations_service: ThirdpartyMigrationsServiceABC = BookstackBookImport(
        attachment_facade=attachment_service,
        directory_service=directory_app_service,
        note_service=app_note_service,
        log=logging_provider,
    )

    directory_service = GrpcDirectoryService(
        directory_service=directory_app_service,
        log=logging_provider,
        to_grpc=grpc_visitor,
        context_factory=user_context_factory,
    )
    add_DirectoryServiceServicer_to_server(directory_service, server)

    grpc_migrations_service = GrpcThirdpartyMigrationsService(
        migrations_service=migrations_service,
        log=logging_provider,
        context_factory=user_context_factory,
    )
    add_ThirdpartyMigrationsServiceServicer_to_server(grpc_migrations_service, server)


    grpc_permission_service = GrpcPermissionService(
        permission_service=permission_service,
        log=logging_provider,
        to_grpc=grpc_visitor,
        context_factory=user_context_factory,
    )
    add_PermissionServiceServicer_to_server(grpc_permission_service, server)

    grpc_sharing_service = GrpcSharingService(
        sharing_service=sharing_service,
        share_access_service=share_access_service,
        log=logging_provider,
        to_grpc=grpc_visitor,
        context_factory=user_context_factory,
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
        context_factory=user_context_factory,
    )
    add_AttachmentServiceServicer_to_server(grpc_attachment_service, server)

    # setup gRPC activity statistics service
    grpc_activity_statistics_service = GrpcActivityStatisticsService(
        statistics_service=activity_statistics_service,
        log=logging_provider,
        to_grpc=grpc_visitor,
        context_factory=user_context_factory,
    )
    add_ActivityStatisticsServiceServicer_to_server(grpc_activity_statistics_service, server)

    # configure server
    listen_addr = f"{grpc_host}:{grpc_port}"
    server.add_insecure_port(listen_addr)
    log.info(f"gRPC server listening on {listen_addr}")

    # Start the server
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())