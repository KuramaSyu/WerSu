"""SpiceDB decorator for :class:`ShelfRepoABC`.

Keeps SpiceDB in sync with the storage row:

* ``insert_shelf`` grants ``shelf#owner`` for the caller.
* ``delete_shelf`` revokes every edge on the deleted shelf (any subject).
* ``add_book`` / ``set_books_of`` (added half) insert
  ``directory:<book>#parent@shelf:<shelf>``.
* ``remove_book`` / ``set_books_of`` (removed half) delete that edge.

Directory ``owner`` / ``admin`` edges are owned by the directory
decorator; this layer does not touch them.

``permission_repo`` is required; the decorator will not run
without it.
"""

from __future__ import annotations

from typing import Any, List, Optional

from src.api import (
    DirectoryRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    ShelfRelationEnum,
    SubjectRef,
)
from src.api.other.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
    unwrap_undefined,
)
from src.api.other.user_context import UserContextABC
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.db.entities.shelf import ShelfEntity


class SpicedbShelfRepoDecorator(ShelfRepoABC):
    """Wrap a :class:`ShelfRepoABC` and keep its SpiceDB edges in sync."""

    def __init__(
        self,
        inner: ShelfRepoABC,
        permission_repo: PermissionRepoABC,
        log_provider: Optional[Any] = None,
    ) -> None:
        self._inner = inner
        self._permission_repo = permission_repo
        self.log = (
            log_provider(__name__, self) if log_provider is not None
            else _NullLogger()
        )

    
    # ---- shelf row CRUD ------------------------------------------------

    async def insert_shelf(
        self,
        *,
        slug: str,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
        user_ctx: Optional[UserContextABC] = None,
    ) -> ShelfEntity:
        if not user_ctx:
            raise ValueError("SpicedbShelfRepoDecorator.insert_shelf requires user_ctx, to not result in inconsistent relations")
        
        entity = await self._inner.insert_shelf(
            slug=slug,
            display_name=display_name,
            description=description,
            image_url=image_url,
            readme_note_id=readme_note_id,
            user_ctx=user_ctx,
        )

        await self._grant_owner(
            user_ctx.user_id,
            unwrap_undefined(entity.id),
        )
        return entity

    async def fetch_shelf(
        self,
        id: str,
        *,
        include_books: bool = False,
    ):
        return await self._inner.fetch_shelf(id, include_books=include_books)

    async def fetch_shelves_by_ids(
        self,
        ids: List[str],
        *,
        include_books: bool = False,
    ):
        return await self._inner.fetch_shelves_by_ids(
            ids, include_books=include_books,
        )

    async def update_shelf(
        self,
        id: str,
        *,
        slug: UndefinedOr[str] = UNDEFINED,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
        user_ctx: Optional[UserContextABC] = None,
    ):
        return await self._inner.update_shelf(
            id,
            slug=slug,
            display_name=display_name,
            description=description,
            image_url=image_url,
            readme_note_id=readme_note_id,
            user_ctx=user_ctx,
        )

    async def delete_shelf(
        self,
        id: str,
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> bool:
        del user_ctx  # the revoke is not caller-scoped
        deleted = await self._inner.delete_shelf(id)
        if deleted:
            await self._revoke_all_shelf_edges(id)
        return deleted

    # ---- shelf <-> book bindings --------------------------------------

    async def set_books_of(
        self,
        shelf_id: str,
        book_ids: List[str],
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> List[str]:
        del user_ctx  # the parent edge is not caller-scoped

        # only remove diff from SpiceDB
        prior = set(await self._inner.get_books_of(shelf_id))
        desired = {str(b) for b in book_ids if b}
        removed = sorted(prior - desired)
        added = await self._inner.set_books_of(shelf_id, book_ids)
        for book_id in added:
            await self._write_book_to_shelf_edge(
                str(shelf_id), str(book_id), insert=True,
            )
        for book_id in removed:
            await self._write_book_to_shelf_edge(
                str(shelf_id), str(book_id), insert=False,
            )
        return added

    async def get_books_of(self, shelf_id: str) -> List[str]:
        return await self._inner.get_books_of(shelf_id)

    async def get_shelves_of_book(self, book_id: str) -> List[str]:
        return await self._inner.get_shelves_of_book(book_id)

    async def add_book(
        self,
        shelf_id: str,
        book_id: str,
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> None:
        del user_ctx  # the parent edge is not caller-scoped
        await self._inner.add_book(shelf_id, book_id)
        await self._write_book_to_shelf_edge(
            shelf_id, unwrap_undefined(book_id), insert=True,
        )

    async def remove_book(
        self,
        shelf_id: str,
        book_id: str,
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> None:
        del user_ctx  # the parent edge is not caller-scoped
        await self._inner.remove_book(shelf_id, book_id)
        await self._write_book_to_shelf_edge(
            shelf_id, unwrap_undefined(book_id), insert=False,
        )

    # ---- helpers -------------------------------------------------------

    async def _grant_owner(
        self,
        user_id: str,
        shelf_id: str,
    ) -> None:
        """Insert ``shelf#owner`` for the caller."""
        edge = Relationship(
            resource=ObjectRef(ObjectTypeEnum.SHELF, str(shelf_id)),
            relation=ShelfRelationEnum.OWNER,
            subject=SubjectRef(ObjectTypeEnum.USER, str(user_id)),
        )
        try:
            await self._permission_repo.insert([edge])
        except Exception as exc:  # noqa: BLE001 -- best-effort auth
            self.log.warning(
                f"shelf owner grant failed for "
                f"shelf={shelf_id!r} user={user_id!r}: "
                f"{type(exc).__name__}: {exc}"
            )

    async def _revoke_all_shelf_edges(self, shelf_id: str) -> None:
        """Delete every SpiceDB edge whose resource is ``shelf_id``.

        ``UNDEFINED`` on relation and subject acts as a wildcard
        for :class:`PermissionRepoABC.delete`.
        """
        try:
            await self._permission_repo.delete(
                Relationship(
                    resource=ObjectRef(ObjectTypeEnum.SHELF, str(shelf_id)),
                    relation=UNDEFINED,
                    subject=SubjectRef(UNDEFINED, UNDEFINED),
                )
            )
        except Exception as exc:  # noqa: BLE001 -- best-effort auth
            self.log.warning(
                f"shelf revoke failed for shelf={shelf_id!r}: "
                f"{type(exc).__name__}: {exc}"
            )

    async def _write_book_to_shelf_edge(
        self,
        shelf_id: str,
        book_id: str,
        *,
        insert: bool,
    ) -> None:
        """Insert or delete ``directory:<book>#parent@shelf:<shelf>``.

        Best-effort with a warning on failure; the storage row
        is already committed by the time we get here.
        """
        if not shelf_id or not book_id:
            return
        edge = Relationship(
            resource=ObjectRef(ObjectTypeEnum.DIRECTORY, str(book_id)),
            relation=DirectoryRelationEnum.PARENT,
            subject=SubjectRef(ObjectTypeEnum.SHELF, str(shelf_id)),
        )
        try:
            if insert:
                await self._permission_repo.insert([edge])
            else:
                await self._permission_repo.delete(edge)
        except Exception as exc:  # noqa: BLE001 -- best-effort auth
            self.log.warning(
                f"shelf parent-edge {'insert' if insert else 'delete'} failed for "
                f"book={book_id!r} shelf={shelf_id!r}: "
                f"{type(exc).__name__}: {exc}"
            )


class _NullLogger:
    """Drop-in logger used when the caller passes no ``log_provider``."""

    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


__all__ = ["SpicedbShelfRepoDecorator"]
