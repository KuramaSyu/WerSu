"""Generic (non-infrastructure) pytest fixtures.

Anything that requires a container (Postgres, SpiceDB, Garage, ...) now
lives under :mod:`tests.fixtures` as its own module.  This module
keeps the lightweight fixtures the rest of the test suite shares:

* :func:`dsn`            Postgres-only bootstrap
* :func:`db`             running db with applied migrations and a clean
                         schema before each test
* :func:`test_user`      a reusable :class:`UserEntity` payload
* :func:`user_repo`      the ``UserRepoABC`` wired against ``db``
* :func:`note_repo_facade`
                         in-memory :class:`NoteFacadeImpl` for unit
                         tests of the note code paths
* The in-memory test doubles (re-exported from
  :mod:`tests.fixtures.fakes`).
"""

from typing import Iterator

import pytest
from testcontainers.postgres import PostgresContainer

from src.ai.embedding_generator import EmbeddingGenerator, Models
from src.db.entities.user.user import UserEntity
from src.db.migrations.context import MigrationContext
from src.db.migrations.runner import MigrationRunner
from src.db.repos import Database, UserPostgresRepo
from src.db.repos.note.combined import CombinedNotePostgresRepo
from src.db.repos.note.content import NoteContentPostgresRepo
from src.db.repos.note.embedding import NoteEmbeddingPostgresRepo
from src.db.repos.note.note_facade import NoteFacadeImpl
from src.api.facades.note_facade import NoteFacadeABC
from src.api.repos.tag_repo import TagRepoABC
from src.db.repos.tag.postgres import PostgresTagRepo
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.in_memory_rule_repo import InMemoryRuleRepo
from tests.stubs.in_memory_shelf_repo import NoopShelfRepo
from src.db.repos.note.versioning import NoteVersionPostgresRepo
from src.db.repos.user.user import UserRepoABC
from src.db.table import Table
from src.utils import logging_provider

# Re-export the in-memory fakes so existing imports
# (``from tests.fixtures import _FakeEmbeddingRepo``) keep working.
from tests._fixtures_pkg.fakes import (  # noqa: F401
    _FakeEmbeddingGenerator,
    _FakeEmbeddingRepo,
    _FakeNoteContentRepo,
    _FakeNoteRepoFacade,
    _FakeJwtProvider,
    _FakeVersionRepo,
    _FakeDatabase,
    _TestDirectoryRepo,
    _TestSpiceDbClient,
)


def create_postgres_dsn(postgres_container: PostgresContainer) -> str:
    """Build a PostgreSQL DSN for a running testcontainer."""
    return (
        f"postgresql://"
        f"{postgres_container.username}:"
        f"{postgres_container.password}@"
        f"{postgres_container.get_container_host_ip()}:"
        f"{postgres_container.get_exposed_port(5432)}/"
        f"{postgres_container.dbname}"
    )


@pytest.fixture(scope="session")
def dsn() -> Iterator[str]:
    """Boot a single Postgres container and yield its DSN for the whole session."""
    container = PostgresContainer(
        image="pgvector/pgvector:0.8.1-pg18-trixie",
        username="postgres",
        password="postgres",
        dbname="testdb",
    )
    container.start()
    dsn = create_postgres_dsn(container)
    yield dsn
    container.stop()


@pytest.fixture(scope="function")
def test_user() -> UserEntity:
    """Reusable UserEntity payload."""
    return UserEntity(
        discord_id=987654321,
        avatar="sldfjseoisjldkj",
        username="KuramaSyu",
        discriminator="1234",
        email="kuramaSyu@example.com",
        type="human",
    )


@pytest.fixture(scope="function")
async def db(dsn):
    """Function-scoped Postgres connection with migrations applied and tables truncated.

    Each test starts with a clean schema, but the Postgres container is
    shared across the session via :func:`dsn`.
    """
    db = Database(dsn, logging_provider, init_file="src/init.sql")
    await db.init_db()

    # Apply migrations for test database setup.
    migration_runner = MigrationRunner(
        ctx=MigrationContext(
            db=db,
            spicedb_client=_TestSpiceDbClient(),
        ),
        log_provider=logging_provider,
    )
    await migration_runner.run_pending_migrations()

    # Reset to a known state.
    await db.execute(
        """
        TRUNCATE TABLE
            auth.user,
            auth.password,
            auth.passkey,
            auth.third_party,
            note.attachment,
            note.attachment_note_link,
            note.attachment_user_link,
            note.directory,
            note.content,
            note.version_snapshot,
            note.version_delta,
            shared,
            user_action,
            activity
        CASCADE;
        """
    )

    yield db
    await db.close()


@pytest.fixture(scope="function")
def directory_repo() -> _TestDirectoryRepo:
    """Return an empty :class:`_TestDirectoryRepo` for tests to seed."""
    return _TestDirectoryRepo()


@pytest.fixture(scope="function")
def note_repo_facade(
    db: Database,
    directory_repo: _TestDirectoryRepo,
    tag_repo: TagRepoABC,
) -> NoteFacadeABC:
    """Return an in-memory :class:`NoteFacadeImpl` for unit tests.

    Uses the in-memory permission repo + directory repo to avoid the
    SpiceDB container dependency.  The :func:`directory_repo` and
    :func:`tag_repo` fixtures are shared so a test can pre-seed
    directories / tags on the same instance the facade consults
    during insert / select.
    """
    common_table_kwargs = {"db": db, "logging_provider": logging_provider}
    content_table = Table(
        **common_table_kwargs,
        table_name="note.content",
        id_fields=["id"],
        error_log=True,
    )
    embedding_table = Table(
        **common_table_kwargs,
        table_name="note.embedding",
        id_fields=["note_id", "model"],
        error_log=True,
    )
    version_snapshot_table = Table(
        **common_table_kwargs,
        table_name="note.version_snapshot",
        id_fields=["snapshot_id"],
        error_log=True,
    )
    version_delta_table = Table(
        **common_table_kwargs,
        table_name="note.version_delta",
        id_fields=["delta_id"],
        error_log=True,
    )
    version_repo = NoteVersionPostgresRepo(
        snapshot_table=version_snapshot_table,
        delta_table=version_delta_table,
        max_deltas_per_snapshot=2,
    )

    return NoteFacadeImpl(
        db=db,
        content_repo=NoteContentPostgresRepo(content_table),
        combined_repo=CombinedNotePostgresRepo(db=db),
        embedding_repo=NoteEmbeddingPostgresRepo(
            table=embedding_table,
            embedding_generator=EmbeddingGenerator(
                model_name=Models.MINI_LM_L6_V2,
                logging_provider=logging_provider,
            ),
        ),
        # TODO: testing with SpiceDB could get hard. Maybe make a Fake
        # which does not do any checks.
        permission_repo=InMemoryPermissionRepo(),
        directory_repo=directory_repo,
        tag_repo=tag_repo,
        logging_provider=logging_provider,
        version_repo=version_repo,
        shelf_repo=NoopShelfRepo(),
        rule_repo=InMemoryRuleRepo(),
    )


@pytest.fixture(scope="function")
async def user_repo(db: Database) -> UserRepoABC:
    """Return the Postgres-backed user repo wired to the function-scoped db."""
    return UserPostgresRepo(
        table=Table(
            db=db,
            table_name="auth.user",
            id_fields=["id"],
            logging_provider=logging_provider,
        ),
        logging_provider=logging_provider,
    )


@pytest.fixture(scope="function")
async def tag_repo(db: Database) -> TagRepoABC:
    """Return a :class:`PostgresTagRepo` wired to the function-scoped db."""
    common_table_kwargs = {"db": db, "logging_provider": logging_provider}
    return PostgresTagRepo(
        tag_table=Table(
            **common_table_kwargs,
            table_name="note.tag",
            id_fields=["id"],
            error_log=True,
        ),
        note_tag_table=Table(
            **common_table_kwargs,
            table_name="note.note_tag",
            id_fields=["note_id", "tag_id"],
            error_log=True,
        ),
        directory_tag_table=Table(
            **common_table_kwargs,
            table_name="note.directory_tag",
            id_fields=["directory_id", "tag_id"],
            error_log=True,
        ),
        db=db,
    )


@pytest.fixture(scope="function")
async def user_service(
    db: Database,
    user_repo: UserRepoABC,
    note_repo_facade: NoteFacadeABC,
    directory_repo: _TestDirectoryRepo,
    tag_repo: TagRepoABC,
):
    """Return a :class:`UserServiceImpl` wired against real DB + in-memory fakes.

    Uses the Postgres-backed shelf repo (rows live in the DB and
    ``create_user`` must persist them) and the same in-memory
    permission + rule repos that :func:`note_repo_facade`
    consults.  The directory facade's ``directory_repo`` is the
    in-memory :class:`_TestDirectoryRepo` -- it accepts the
    bootstrap inserts and its
    :meth:`_TestDirectoryRepo.list_user_directory_ids` falls
    back to scanning the shared permission repo for
    ``directory#admin@user`` relations, so the rule's resolved
    ``directory_id`` is accepted as visible to the user.  The
    test directory repo is wired to the shared permission repo
    so the fallback kicks in.

    Tests that need the full bootstrap (shelf + 3 books +
    default-fleeting rule) call
    :meth:`UserServiceImpl.create_user` instead of poking at
    the user repo + facade rule repo + facade permission repo
    individually.
    """
    from src.db.repos.directory.directory_facade import DirectoryFacadeImpl
    from src.db.repos.shelf.postgres import PostgresShelfRepo
    from src.db.repos.user import RepoContextFactory
    from src.services.user_service import UserServiceImpl
    permission_repo = note_repo_facade._permission_repo  # type: ignore[attr-defined]
    rule_repo = note_repo_facade._rule_repo  # type: ignore[attr-defined]
    directory_repo._permission_repo = permission_repo
    shelf_repo = PostgresShelfRepo(
        shelf_table=Table(
            db=db,
            table_name="note.shelf",
            id_fields=["id"],
            error_log=True,
            logging_provider=logging_provider,
        ),
        shelf_book_table=Table(
            db=db,
            table_name="note.shelf_book",
            id_fields=["shelf_id", "book_id"],
            error_log=True,
            logging_provider=logging_provider,
        ),
    )
    directory_facade = DirectoryFacadeImpl(
        directory_repo=directory_repo,
        permission_repo=permission_repo,
        tag_repo=tag_repo,
        log=logging_provider,
        shelf_repo=shelf_repo,
    )
    context_factory = RepoContextFactory(user_repo=user_repo)
    return UserServiceImpl(
        user_repo=user_repo,
        directory_facade=directory_facade,
        context_factory=context_factory,
        shelf_repo=shelf_repo,
        rule_repo=rule_repo,
        permission_repo=permission_repo,
    )
