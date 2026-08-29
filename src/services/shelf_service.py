"""Concrete :class:`~src.api.services.shelf_service.ShelfServiceABC` implementation.

Every method except :meth:`create_shelf` is gated on a
:class:`PermissionCheckChain` from
:mod:`src.domain.permission_chain`.  The chain checks live on the
service because the service owns the permission repo -- pushing
them down to the repo would couple storage to policy.

Permission policy:

* ``create_shelf`` -- no chain; the service inserts
  ``shelf#owner@user:<actor>`` so the creator has full rights.
* ``get_shelf`` / ``get_shelves`` / ``list_shelves`` /
  ``get_books_of_shelf`` / ``get_shelves_of_book`` -- require
  ``shelf#view``.
* ``update_shelf`` / ``set_books`` / ``attach_book`` /
  ``detach_book`` -- require ``shelf#write``.
* ``delete_shelf`` -- require ``shelf#delete``.

The implementation is intentionally thin: every write goes
through one of the injected repos; the service's job is
composition + the policy boundary.
"""

from __future__ import annotations

from typing import List, Optional

from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.relationship import (
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    ShelfRelationEnum,
    SubjectRef,
)
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.other.user_context import UserContextABC
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.repos.rule_repo import RuleRepoABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.api.services.shelf_service import (
    BootstrapResult,
    BootstrapStrategy,
    DryDeleteResult,
    ShelfEntity,
    ShelfPermissionError,
    ShelfReadOptions,
    ShelfServiceABC,
    resolve_shelf_read_options,
)
from src.domain.permission_chain import (
    HasShelfDeletePerm,
    HasShelfViewPerm,
    HasShelfWritePerm,
    PermissionCheckChainStart,
)
from src.services.shelf_bootstrap import build_strategy


class ShelfServiceImpl(ShelfServiceABC):
    """Thin service-layer implementation.

    Args:
        shelf_repo: storage layer.
        permission_repo: permission repo -- gates every method
            except :meth:`create_shelf`; also used to insert the
            ``shelf#owner@user:<actor>`` relation on create.
        directory_facade: directory facade -- passed through to
            the bootstrap strategies that need to create books.
        rule_repo: rule repo -- passed through to the bootstrap
            strategies (the default-fleeting rule lives there).
        logging_provider: optional logger factory.
    """

    def __init__(
        self,
        shelf_repo: ShelfRepoABC,
        permission_repo: PermissionRepoABC,
        directory_facade: DirectoryFacadeABC,
        rule_repo: RuleRepoABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._shelf_repo = shelf_repo
        self._permission_repo = permission_repo
        self._directory_facade = directory_facade
        self._rule_repo = rule_repo
        self._logging_provider = logging_provider

    # ---- create ----------------------------------------------------------

    async def create_shelf(
        self,
        entity: ShelfEntity,
        actor: UserContextABC,
        *,
        bootstrap: BootstrapStrategy = BootstrapStrategy.NONE,
    ) -> tuple[ShelfEntity, BootstrapResult]:
        """Insert the shelf row + run an optional bootstrap strategy.

        Creation is unconditional (no permission chain); matches
        the rule service's policy.  The shelf repo's
        :func:`~src.db.repos.shelf.postgres.writes_user_permissions`
        decorator writes the ``shelf#owner`` / ``shelf#admin``
        edges for ``actor`` on the freshly inserted row, so the
        service no longer has to.
        """
        if not entity.slug:
            raise ValueError("shelf.slug is required for create_shelf")

        persisted = await self._shelf_repo.insert_shelf(
            slug=str(entity.slug),
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            readme_note_id=entity.readme_note_id,
            user_ctx=actor,
        )

        if bootstrap == BootstrapStrategy.NONE:
            return persisted, BootstrapResult()

        strategy = build_strategy(
            bootstrap.value,
            shelf_repo=self._shelf_repo,
            rule_repo=self._rule_repo,
            directory_facade=self._directory_facade,
        )
        bootstrap_result = await strategy.apply(
            shelf=persisted,
            owner_id=str(actor.user_id),
            user_ctx=actor,
        )
        return persisted, bootstrap_result

    # ---- read ------------------------------------------------------------

    async def get_shelf(
        self,
        shelf_id: str,
        actor: UserContextABC,
        *,
        options: Optional[ShelfReadOptions] = None,
    ) -> Optional[ShelfEntity]:
        """Load a shelf the caller can view."""
        if not shelf_id:
            raise ValueError("shelf_id is required for get_shelf")

        opts = resolve_shelf_read_options(options)
        entity = await self._shelf_repo.fetch_shelf(
            str(shelf_id), include_books=bool(opts.get("include_books")),
        )
        if entity is None:
            return None
        await self._enforce_view_permission(str(shelf_id), actor)
        return entity

    async def get_shelves(
        self,
        ids: List[str],
        actor: UserContextABC,
        *,
        options: Optional[ShelfReadOptions] = None,
    ) -> List[ShelfEntity]:
        """Load many shelves by id, dropping the ones the caller can't view."""
        if not ids:
            return []
        opts = resolve_shelf_read_options(options)
        entities = await self._shelf_repo.fetch_shelves_by_ids(
            [str(i) for i in ids],
            include_books=bool(opts.get("include_books")),
        )
        # Cheap O(n) permission probe; the alternative is a single
        # ``lookup_resources`` SpiceDB round-trip that returns the
        # visible set, but it would also require intersect with the
        # requested ids in Python.  Per-shelf probe matches the
        # rule service's ``list_rules`` pattern.
        visible: List[ShelfEntity] = []
        for entity in entities:
            if entity.id is None or is_undefined(entity.id):
                continue
            try:
                await self._enforce_view_permission(str(entity.id), actor)
            except ShelfPermissionError:
                continue
            visible.append(entity)
        return visible

    async def list_shelves(
        self,
        actor: UserContextABC,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        options: Optional[ShelfReadOptions] = None,
    ) -> List[ShelfEntity]:
        """Return every shelf the caller can view, paginated.

        Uses :meth:`PermissionRepoABC.lookup` to filter by
        ``shelf#view`` -- same pattern as
        :meth:`DirectoryFacadeImpl.list_user_directory_ids`.  The
        page is sliced **after** the permission filter so a
        paginated page cannot leak ids the caller can't see.
        """
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0")
        if offset is not None and offset < 0:
            raise ValueError("offset must be >= 0")

        opts = resolve_shelf_read_options(options)
        shelf_ids = await self._permission_repo.lookup(
            Relationship(
                resource=ObjectRef(
                    object_type=ObjectTypeEnum.SHELF,
                    object_id=UNDEFINED,
                ),
                relation=ShelfRelationEnum.VIEW,
                subject=SubjectRef(
                    object_type=ObjectTypeEnum.USER,
                    object_id=str(actor.user_id),
                ),
            )
        )

        page = list(shelf_ids)
        if offset:
            page = page[offset:]
        if limit is not None:
            page = page[:limit]
        if not page:
            return []

        return await self._shelf_repo.fetch_shelves_by_ids(
            page, include_books=bool(opts.get("include_books")),
        )

    # ---- update ----------------------------------------------------------

    async def update_shelf(
        self,
        entity: ShelfEntity,
        actor: UserContextABC,
    ) -> ShelfEntity:
        """Partially update an existing shelf.

        Permission check runs against the entity's id, not its
        contents -- callers can't sneak past the gate by sending
        a row whose id they don't own.
        """
        if not entity.id or is_undefined(entity.id):
            raise ValueError("shelf.id is required for update_shelf")
        shelf_id = str(entity.id)
        await self._enforce_write_permission(shelf_id, actor)

        updated = await self._shelf_repo.update_shelf(
            shelf_id,
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            readme_note_id=entity.readme_note_id,
        )
        if updated is None:
            raise ValueError(f"shelf not found: {shelf_id}")
        return updated

    # ---- delete ----------------------------------------------------------

    async def delete_shelf(
        self,
        shelf_id: str,
        actor: UserContextABC,
        *,
        dry: bool = False,
    ) -> Optional[DryDeleteResult]:
        """Delete a shelf, optionally dry-run."""
        if not shelf_id:
            raise ValueError("shelf_id is required for delete_shelf")
        shelf_id = str(shelf_id)
        await self._enforce_delete_permission(shelf_id, actor)

        if dry:
            book_ids = await self._shelf_repo.get_books_of(shelf_id)
            return DryDeleteResult(
                affected_book_ids=list(book_ids),
                binding_count=len(book_ids),
            )

        await self._shelf_repo.delete_shelf(shelf_id)
        return None

    # ---- book bindings ---------------------------------------------------

    async def set_books(
        self,
        shelf_id: str,
        book_ids: List[str],
        actor: UserContextABC,
    ) -> None:
        """Replace the full book set of ``shelf_id``."""
        if not shelf_id:
            raise ValueError("shelf_id is required for set_books")
        await self._enforce_write_permission(str(shelf_id), actor)
        await self._shelf_repo.set_books_of(
            str(shelf_id), [str(b) for b in book_ids if b],
            user_ctx=actor,
        )

    async def attach_book(
        self,
        shelf_id: str,
        book_id: str,
        actor: UserContextABC,
    ) -> None:
        """Idempotently bind ``book_id`` to ``shelf_id``."""
        if not shelf_id:
            raise ValueError("shelf_id is required for attach_book")
        if not book_id:
            raise ValueError("book_id is required for attach_book")
        await self._enforce_write_permission(str(shelf_id), actor)
        await self._shelf_repo.add_book(
            str(shelf_id), str(book_id), user_ctx=actor,
        )

    async def detach_book(
        self,
        shelf_id: str,
        book_id: str,
        actor: UserContextABC,
    ) -> None:
        """Remove the ``book_id`` binding from ``shelf_id``."""
        if not shelf_id:
            raise ValueError("shelf_id is required for detach_book")
        if not book_id:
            raise ValueError("book_id is required for detach_book")
        await self._enforce_write_permission(str(shelf_id), actor)
        await self._shelf_repo.remove_book(
            str(shelf_id), str(book_id), user_ctx=actor,
        )

    async def get_books_of_shelf(
        self,
        shelf_id: str,
        actor: UserContextABC,
    ) -> List[str]:
        """Return book ids on ``shelf_id`` the caller can view."""
        if not shelf_id:
            raise ValueError("shelf_id is required for get_books_of_shelf")
        await self._enforce_view_permission(str(shelf_id), actor)
        return await self._shelf_repo.get_books_of(str(shelf_id))

    async def get_shelves_of_book(
        self,
        book_id: str,
        actor: UserContextABC,
    ) -> List[str]:
        """Return shelf ids that contain ``book_id``.

        Filters by view-permission: the result is the intersection
        of every shelf that holds ``book_id`` and every shelf the
        caller can view.  Empty when the caller cannot view any of
        the candidate shelves.
        """
        if not book_id:
            raise ValueError("book_id is required for get_shelves_of_book")
        candidate_ids = await self._shelf_repo.get_shelves_of_book(
            str(book_id),
        )
        visible: List[str] = []
        for shelf_id in candidate_ids:
            try:
                await self._enforce_view_permission(str(shelf_id), actor)
            except ShelfPermissionError:
                continue
            visible.append(str(shelf_id))
        return visible

    # ---- internal helpers -----------------------------------------------

    async def _enforce_view_permission(
        self,
        shelf_id: str,
        actor: UserContextABC,
    ) -> None:
        chain = (
            PermissionCheckChainStart(self._permission_repo)
            .set_next(HasShelfViewPerm(shelf_id))
        )
        result = await chain.check(actor)
        if not result:
            raise ShelfPermissionError(str(result.error))

    async def _enforce_write_permission(
        self,
        shelf_id: str,
        actor: UserContextABC,
    ) -> None:
        chain = (
            PermissionCheckChainStart(self._permission_repo)
            .set_next(HasShelfWritePerm(shelf_id))
        )
        result = await chain.check(actor)
        if not result:
            raise ShelfPermissionError(str(result.error))

    async def _enforce_delete_permission(
        self,
        shelf_id: str,
        actor: UserContextABC,
    ) -> None:
        chain = (
            PermissionCheckChainStart(self._permission_repo)
            .set_next(HasShelfDeletePerm(shelf_id))
        )
        result = await chain.check(actor)
        if not result:
            raise ShelfPermissionError(str(result.error))


__all__ = ["ShelfServiceImpl"]