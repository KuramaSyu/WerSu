from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class MigrationContext:
    """Dependency container passed to migrations.

    Parameters
    ----------
    db : Any
        Database abstraction used by SQL migrations.
    spicedb_client : Any | None, optional
        SpiceDB client instance for auth schema migrations.
    services : dict[str, Any], optional
        Additional named dependencies available to migrations.
    """

    db: Any
    spicedb_client: Optional[Any] = None

    # I know that a serivce provider is kinda an anti-pattern,
    # but migrations will need to have the some signatures everywhere
    # e.g. no custom instructor with the args needed to be able
    # to run all migrations automated
    services: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str, default: Any = None) -> Any:
        """Return an extra dependency by name.

        Parameters
        ----------
        name : str
            Dependency key.
        default : Any, optional
            Default value when the key does not exist.

        Returns
        -------
        Any
            Stored dependency value or ``default``.
        """
        return self.services.get(name, default)
