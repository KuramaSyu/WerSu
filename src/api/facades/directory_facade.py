"""Abstract repository contract for directories.

Lives in :mod:`src.api` because it is the cross-layer contract
the service layer depends on; the concrete Postgres + SpiceDB
implementation under :mod:`src.db.repos.directory` is hidden behind
it.

Implementations:
    * :class:`src.db.repos.directory.directory.DirectoryFacadeImpl`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Optional, Sequence

from src.api.other.user_context import UserContextABC
from src.api.repos.directory_repo import DirectoryHelperMixin
from src.db.entities.directory.directory import DirectoryEntity


if TYPE_CHECKING:
    from src.api.services.directory_service import DirectoryIncludeOptions


@dataclass(frozen=True)
class DefaultDirectorySpec:
    """Specification for one default zettelkasten directory.

    Attributes:
        name: machine-readable slug (e.g. ``"fleeting_notes"``).
        display_name: human-readable label.
        description: one-sentence purpose statement shown to users.
    """

    name: str
    display_name: str
    description: str


class DirectoryFacadeABC(DirectoryHelperMixin):
    """Facade to insert relations simultaneously into Postgres and SpiceDB
    to ensure somehow consistency. Otherwise this facade could be replaced
    with just one DirectoryRepoABC implementation.
    
    Implements:
        * :class:`~src.api.repos.directory_repo.DirectoryHelperMixin`

    Implementations:
        * :class:`~src.db.repos.directory.directory.DirectoryFacadeImpl`
    """

    DEFAULT_DIRECTORY_SPECS: ClassVar[Sequence[DefaultDirectorySpec]] = (
        DefaultDirectorySpec(
            name="fleeting_notes",
            display_name="Fleeting Notes",
            description=(
                "Capture quick, raw thoughts with minimal friction. "
                "In the zettelkasten flow, these are temporary inbox notes "
                "to revisit, refine, or discard soon."
            ),
        ),
        DefaultDirectorySpec(
            name="literature_notes",
            display_name="Literature Notes",
            description=(
                "Store notes extracted from sources like books, papers, and articles. "
                "In zettelkasten, literature notes summarize references in your own words "
                "before transforming them into permanent notes."
            ),
        ),
        DefaultDirectorySpec(
            name="permanent_notes",
            display_name="Permanent Notes",
            description=(
                "Keep evergreen, atomic ideas that connect to other notes over time. "
                "These are the durable knowledge units in a zettelkasten, written clearly "
                "for future reuse and linking."
            ),
        ),
    )

    def get_default_directory_specs(self) -> Sequence[DefaultDirectorySpec]:
        """Return the default zettelkasten directory specifications.

        Returns:
            Sequence[DefaultDirectorySpec]: immutable specs the
            bootstrap code uses when creating a new user's tree.
        """
        return self.DEFAULT_DIRECTORY_SPECS

    @abstractmethod
    async def create_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: UserContextABC,
    ) -> DirectoryEntity:
        """Create a directory and ensure that permissions are set.

        Args:
            entity: payload carrying the directory's Postgres fields
                and (optionally) the SpiceDB relations to write.
            user_ctx: the user to which this directory will be linked as admin.

        Returns:
            DirectoryEntity: the created entity with its
            server-generated id populated.
        """
        ...

    @abstractmethod
    async def fetch_directory(
        self,
        id: str,
        *,
        include: Optional["DirectoryIncludeOptions"] = None,
    ) -> Optional[DirectoryEntity]:
        """Fetch a directory by id, optionally hydrated.

        Args:
            id: directory id.
            include: opt-in enrichment flags; see
                :class:`~src.api.directory_service.DirectoryIncludeOptions`.
                When omitted (or every flag ``False``) only the row
                + its SpiceDB relations are returned.

        Returns:
            Optional[DirectoryEntity]: the directory plus its
            SpiceDB relations, or ``None`` when no row matches.
            List / count fields are populated iff their flag was
            set; everything else stays at
            :obj:`~src.api.undefined.UNDEFINED`.
        """
        ...

    @abstractmethod
    async def update_directory(
        self,
        entity: DirectoryEntity,
    ) -> Optional[DirectoryEntity]:
        """Partially update a directory by id.

        Args:
            entity: directory carrying the id plus the fields to
                overwrite.  Fields set to
                :obj:`~src.api.undefined.UNDEFINED` are left
                unchanged; explicit ``None`` clears the column.

        Returns:
            Optional[DirectoryEntity]: the updated entity, or
            ``None`` when no row matches the id.

        Raises:
            ValueError: ``entity.id`` is :obj:`~src.api.undefined.UNDEFINED`
                or ``None``.
        """
        ...

    @abstractmethod
    async def delete_directory(
        self,
        entity: DirectoryEntity,
    ) -> bool:
        """Delete a directory and its SpiceDB relations.

        Args:
            entity: directory carrying at least its ``id``.

        Returns:
            bool: ``True`` when exactly one row was removed from
            Postgres; ``False`` when no row matched.

        Raises:
            ValueError: ``entity.id`` is :obj:`~src.api.undefined.UNDEFINED`
                or ``None``.
        """
        ...


__all__ = ["DefaultDirectorySpec", "DirectoryFacadeABC"]