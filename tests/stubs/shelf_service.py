"""In-memory :class:`ShelfServiceABC` stub for unit tests.

The stub records every call so test assertions can target a
specific caller (and its arguments) without re-implementing the
whole :class:`~src.api.services.shelf_service.ShelfServiceABC` in
every test file.  Per-method ``*_deny`` flags force the stub to
raise :exc:`ShelfPermissionError` so the gRPC adapter's
permission-denied branches remain testable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import UserContextABC
from src.api.services.shelf_service import (
    BootstrapResult,
    BootstrapStrategy,
    DryDeleteResult,
    ShelfEntity,
    ShelfPermissionError,
    ShelfReadOptions,
    ShelfServiceABC,
)


class _StubShelfService(ShelfServiceABC):
    """In-memory :class:`ShelfServiceABC` used by gRPC adapter tests."""

    def __init__(self) -> None:
        self.shelves_by_id: Dict[str, ShelfEntity] = {}
        self.next_shelf_id = 0

        # Last-call probes for each method.
        self.last_create_user_id: Optional[str] = None
        self.last_create_bootstrap: Optional[BootstrapStrategy] = None
        self.last_create_entity: Optional[ShelfEntity] = None
        self.last_get_id: Optional[str] = None
        self.last_get_user_id: Optional[str] = None
        self.last_get_options: Optional[ShelfReadOptions] = None
        self.last_get_shelves_ids: List[str] = []
        self.last_get_shelves_user_id: Optional[str] = None
        self.last_list_user_id: Optional[str] = None
        self.last_list_limit: Optional[int] = None
        self.last_list_offset: Optional[int] = None
        self.last_list_options: Optional[ShelfReadOptions] = None
        self.last_update_user_id: Optional[str] = None
        self.last_update_entity: Optional[ShelfEntity] = None
        self.last_delete_id: Optional[str] = None
        self.last_delete_user_id: Optional[str] = None
        self.last_delete_dry: Optional[bool] = None
        self.delete_result: Optional[DryDeleteResult] = None
        self.last_set_books_shelf_id: Optional[str] = None
        self.last_set_books_book_ids: List[str] = []
        self.last_set_books_user_id: Optional[str] = None
        self.last_attach_shelf_id: Optional[str] = None
        self.last_attach_book_id: Optional[str] = None
        self.last_detach_shelf_id: Optional[str] = None
        self.last_detach_book_id: Optional[str] = None
        self.last_get_books_of_shelf_id: Optional[str] = None
        self.last_get_shelves_of_book_id: Optional[str] = None
        self.next_bootstrap_result: BootstrapResult = BootstrapResult()

        # Per-method deny flags.
        self.create_deny = False
        self.get_deny = False
        self.get_shelves_deny = False
        self.list_deny = False
        self.update_deny = False
        self.delete_deny = False
        self.set_books_deny = False
        self.attach_book_deny = False
        self.detach_book_deny = False
        self.get_books_of_shelf_deny = False
        self.get_shelves_of_book_deny = False

    # ---- helpers ----------------------------------------------------

    def _mint_id(self) -> str:
        self.next_shelf_id += 1
        return f"shelf-{self.next_shelf_id}"

    # ---- CRUD --------------------------------------------------------

    async def create_shelf(
        self,
        entity: ShelfEntity,
        actor: UserContextABC,
        *,
        bootstrap: BootstrapStrategy = BootstrapStrategy.NONE,
    ) -> tuple[ShelfEntity, BootstrapResult]:
        self.last_create_entity = entity
        self.last_create_user_id = actor.user_id
        self.last_create_bootstrap = bootstrap
        if self.create_deny:
            raise ShelfPermissionError("not allowed")
        shelf_id = self._mint_id()
        created = ShelfEntity(
            id=shelf_id,
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            readme_note_id=entity.readme_note_id,
        )
        self.shelves_by_id[shelf_id] = created
        return created, self.next_bootstrap_result

    async def get_shelf(
        self,
        shelf_id: str,
        actor: UserContextABC,
        *,
        options: Optional[ShelfReadOptions] = None,
    ) -> Optional[ShelfEntity]:
        self.last_get_id = shelf_id
        self.last_get_user_id = actor.user_id
        self.last_get_options = options
        if self.get_deny:
            raise ShelfPermissionError("not allowed")
        return self.shelves_by_id.get(str(shelf_id))

    async def get_shelves(
        self,
        ids: List[str],
        actor: UserContextABC,
        *,
        options: Optional[ShelfReadOptions] = None,
    ) -> List[ShelfEntity]:
        self.last_get_shelves_ids = list(ids)
        self.last_get_shelves_user_id = actor.user_id
        self.last_get_options = options
        if self.get_shelves_deny:
            raise ShelfPermissionError("not allowed")
        out: List[ShelfEntity] = []
        for sid in ids:
            shelf = self.shelves_by_id.get(str(sid))
            if shelf is not None:
                out.append(shelf)
        return out

    async def list_shelves(
        self,
        actor: UserContextABC,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        options: Optional[ShelfReadOptions] = None,
    ) -> List[ShelfEntity]:
        self.last_list_user_id = actor.user_id
        self.last_list_limit = limit
        self.last_list_offset = offset
        self.last_list_options = options
        if self.list_deny:
            raise ShelfPermissionError("not allowed")
        return list(self.shelves_by_id.values())

    async def update_shelf(
        self,
        entity: ShelfEntity,
        actor: UserContextABC,
    ) -> ShelfEntity:
        self.last_update_entity = entity
        self.last_update_user_id = actor.user_id
        if self.update_deny:
            raise ShelfPermissionError("not allowed")
        existing = self.shelves_by_id.get(str(entity.id))
        if existing is None:
            raise ValueError(f"shelf not found: {entity.id}")
        if entity.slug is not UNDEFINED:
            existing.slug = entity.slug
        if entity.display_name is not UNDEFINED:
            existing.display_name = entity.display_name
        if entity.description is not UNDEFINED:
            existing.description = entity.description
        if entity.image_url is not UNDEFINED:
            existing.image_url = entity.image_url
        if entity.readme_note_id is not UNDEFINED:
            existing.readme_note_id = entity.readme_note_id
        return existing

    async def delete_shelf(
        self,
        shelf_id: str,
        actor: UserContextABC,
        *,
        dry: bool = False,
    ) -> Optional[DryDeleteResult]:
        self.last_delete_id = shelf_id
        self.last_delete_user_id = actor.user_id
        self.last_delete_dry = dry
        if self.delete_deny:
            raise ShelfPermissionError("not allowed")
        if dry:
            return self.delete_result or DryDeleteResult()
        existed = self.shelves_by_id.pop(str(shelf_id), None) is not None
        return None if existed else None

    # ---- book bindings ----------------------------------------------

    async def set_books(
        self,
        shelf_id: str,
        book_ids: List[str],
        actor: UserContextABC,
    ) -> None:
        self.last_set_books_shelf_id = shelf_id
        self.last_set_books_book_ids = list(book_ids)
        self.last_set_books_user_id = actor.user_id
        if self.set_books_deny:
            raise ShelfPermissionError("not allowed")

    async def attach_book(
        self,
        shelf_id: str,
        book_id: str,
        actor: UserContextABC,
    ) -> None:
        self.last_attach_shelf_id = shelf_id
        self.last_attach_book_id = book_id
        self.last_attach_user_id = actor.user_id  # type: ignore[attr-defined]
        if self.attach_book_deny:
            raise ShelfPermissionError("not allowed")

    async def detach_book(
        self,
        shelf_id: str,
        book_id: str,
        actor: UserContextABC,
    ) -> None:
        self.last_detach_shelf_id = shelf_id
        self.last_detach_book_id = book_id
        self.last_detach_user_id = actor.user_id  # type: ignore[attr-defined]
        if self.detach_book_deny:
            raise ShelfPermissionError("not allowed")

    async def get_books_of_shelf(
        self,
        shelf_id: str,
        actor: UserContextABC,
    ) -> List[str]:
        self.last_get_books_of_shelf_id = shelf_id
        self.last_get_books_of_shelf_user_id = actor.user_id  # type: ignore[attr-defined]
        if self.get_books_of_shelf_deny:
            raise ShelfPermissionError("not allowed")
        return []

    async def get_shelves_of_book(
        self,
        book_id: str,
        actor: UserContextABC,
    ) -> List[str]:
        self.last_get_shelves_of_book_id = book_id
        self.last_get_shelves_of_book_user_id = actor.user_id  # type: ignore[attr-defined]
        if self.get_shelves_of_book_deny:
            raise ShelfPermissionError("not allowed")
        return []


__all__ = ["_StubShelfService"]