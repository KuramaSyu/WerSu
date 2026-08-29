from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.user_context import ContextFactory
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.repos.rule_repo import RuleRepoABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.services.shelf_bootstrap import ShelfBootstrapStrategy


@dataclass
class MigrationServices:
    """Typed dependency bundle handed to every migration.

    Every field is ``Optional`` because some callers run
    migrations without a full service bundle (e.g. fixture-only
    CLI runs that only touch Postgres).  Migrations branch on
    ``None`` instead of crashing on ``AttributeError``.
    """

    #: SpiceDB-backed permission repo.  Used by migrations that
    #: write auth relations (e.g. shelf owner/admin edges).
    permission_repo: Optional[PermissionRepoABC] = None

    #: Postgres rule repo.  Used by migrations that create
    #: default routing rules on a freshly inserted shelf.
    rule_repo: Optional[RuleRepoABC] = None

    #: Postgres shelf repo.  Used by migrations that probe /
    #: bind books on a freshly inserted shelf.
    shelf_repo: Optional[ShelfRepoABC] = None

    #: Directory facade.  Used by migrations that create
    #: default books through the live
    #: ``DirectoryFacadeABC.create_directory`` contract instead
    #: of reimplementing the SpiceDB edge writes.
    directory_facade: Optional[DirectoryFacadeABC] = None

    #: User context factory.  Used by migrations that need to
    #: impersonate a user when calling services which require a
    #: caller identity (e.g.
    #: ``DirectoryFacadeABC.create_directory``).
    user_context_factory: Optional["ContextFactory[Any]"] = None

    #: Pre-bound
    #: :class:`~src.services.shelf_bootstrap.zettelkasten.ZettelkastenStrategy`
    #: instance.  Migrations that need to run the standard
    #: "create three default books + the default routing rule"
    #: recipe should call ``strategy.apply(...)`` instead of
    #: reimplementing the recipe in raw SQL.
    zettelkasten_strategy: Optional[ShelfBootstrapStrategy] = None


@dataclass
class MigrationContext:
    """Dependency container passed to migrations.

    Parameters
    ----------
    db : Any
        Database abstraction used by SQL migrations.
    spicedb_client : Any | None, optional
        SpiceDB client instance for auth schema migrations.
    services : MigrationServices, optional
        Additional named dependencies available to migrations.
        Every field on :class:`MigrationServices` is
        ``Optional``; tests / fixtures that don't run the full
        service bundle may pass ``MigrationServices()`` with no
        arguments.
    """

    db: Any
    spicedb_client: Optional[Any] = None

    # I know that a serivce provider is kinda an anti-pattern,
    # but migrations will need to have the some signatures everywhere
    # e.g. no custom instructor with the args needed to be able
    # to run all migrations automated
    services: MigrationServices = field(default_factory=MigrationServices)


__all__ = [
    "MigrationContext",
    "MigrationServices",
]

