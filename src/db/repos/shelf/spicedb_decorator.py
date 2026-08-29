"""SpiceDB-permission decorator for :class:`ShelfRepoABC`.

Wraps an inner :class:`ShelfRepoABC` (typically
:class:`~src.db.repos.shelf.postgres.PostgresShelfRepo`) and,
on every successful write, inserts the matching
``shelf#owner`` / ``shelf#admin`` / ``directory#owner`` /
``directory#admin`` edges into SpiceDB for the caller
identified by ``user_ctx``.

Why a class instead of a function-level decorator on
``PostgresShelfRepo``?

* Pure storage stays pure.  The Postgres repo can be wired
  into fixture-only test suites that never talk to SpiceDB
  -- as long as those tests also avoid passing ``user_ctx``,
  the decorator no-ops on every write.
* Composition root controls the wiring.  Production boots
  wrap the Postgres repo with this class; tests that don't
  need SpiceDB edges can either skip the wrapper or
  construct one without a ``permission_repo``.
* The decorator's auth policy is auditable from one place.
  Every SpiceDB edge this class writes is enumerated in
  :meth:`_edges_for_op` -- one file, one grep.

If a caller passes ``user_ctx`` while the decorator has no
``permission_repo`` configured, :meth:`_write_user_edges`
raises :class:`RuntimeError` -- silently dropping the grant
would be a security bug, so the decorator fails loud.

Class structure mirrors :class:`ShelfRepoABC` 1:1.  Read
methods pass through unchanged.  Write methods forward
``user_ctx`` to the inner repo and, when supplied, route
through :meth:`_write_user_edges` to grant the matching
SpiceDB edges for the caller.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
)
from src.api.other.user_context import UserContextABC
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.db.entities.shelf import ShelfEntity


def _shelf_owner_admin(shelf_id: str, user_id: str) -> List[Relationship]:
    """Build the ``shelf#owner`` + ``shelf#admin`` edge list."""
    return [
        Relationship(
            resource=ObjectRef(
                object_type=ObjectTypeEnum.SHELF,
                object_id=str(shelf_id),
            ),
            relation=rel,
            subject=SubjectRef(
                object_type=ObjectTypeEnum.USER,
                object_id=str(user_id),
            ),
        )
        for rel in (ShelfRelationEnum.OWNER, ShelfRelationEnum.ADMIN)
    ]


def _book_owner_admin(book_id: str, user_id: str) -> List[Relationship]:
    """Build the ``directory#owner`` + ``directory#admin`` edge list.

    A "book" in the shelf vocabulary is a directory, so the
    resource type is :data:`ObjectTypeEnum.DIRECTORY` and the
    relations live on the directory schema.  See
    ``src/db/migrations/schema.zed``.
    """
    return [
        Relationship(
            resource=ObjectRef(
                object_type=ObjectTypeEnum.DIRECTORY,
                object_id=str(book_id),
            ),
            relation=rel,
            subject=SubjectRef(
                object_type=ObjectTypeEnum.USER,
                object_id=str(user_id),
            ),
        )
        for rel in (DirectoryRelationEnum.OWNER, DirectoryRelationEnum.ADMIN)
    ]


class SpicedbShelfRepoDecorator(ShelfRepoABC):
    """Wrap a :class:`ShelfRepoABC` and grant SpiceDB edges after every write.

    Args:
        inner: the storage repo to delegate to.  Reads and
            writes both pass through; this class only adds
            post-write SpiceDB grants for the caller.
        permission_repo: SpiceDB adapter used to insert the
            edges.  ``None`` is only safe for callers that
            never pass ``user_ctx`` (e.g. fixture-only tests
            that exercise the bare storage path).  If a caller
            supplies ``user_ctx`` while ``permission_repo`` is
            ``None`` the decorator raises :class:`RuntimeError`
            -- silently dropping the grant would be a security
            bug.
        log_provider: optional callable returning a structured
            logger -- the decorator logs SpiceDB errors here so
            a flaky SpiceDB does not take down the Postgres
            write that already succeeded.
    """

    def __init__(
        self,
        inner: ShelfRepoABC,
        permission_repo: Optional[PermissionRepoABC],
        log_provider: Optional[Any] = None,
    ) -> None:
        self._inner = inner
        self._permission_repo = permission_repo
        self.log = (
            log_provider(__name__, self) if log_provider is not None
            else _NullLogger()
        )

    # ---- shelf row CRUD -------------------------------------------------

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
        entity = await self._inner.insert_shelf(
            slug=slug,
            display_name=display_name,
            description=description,
            image_url=image_url,
            readme_note_id=readme_note_id,
            user_ctx=user_ctx,
        )
        await self._write_user_edges(
            op="insert_shelf",
            result=entity,
            user_ctx=user_ctx,
            call_kwargs={"shelf_id": str(getattr(entity, "id", "") or "")},
        )
        return entity

    async def fetch_shelf(
        self,
        id: str,
        *,
        include_books: bool = False,
    ):
        return await self._inner.fetch_shelf(
            id, include_books=include_books,
        )

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
        # Intentionally no SpiceDB grant: callers normally
        # delete a shelf before its owner edges would matter,
        # and the delete service path enforces delete-permission
        # upstream.  Keep the branch explicit so the auth policy
        # stays declarative -- if delete semantics change,
        # add the edges here.
        return await self._inner.delete_shelf(id, user_ctx=user_ctx)

    # ---- shelf <-> book bindings ---------------------------------------

    async def set_books_of(
        self,
        shelf_id: str,
        book_ids: List[str],
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> List[str]:
        # Delegate to the inner repo and let it report which
        # books were newly added via the return value.  The
        # :class:`PostgresShelfRepo` already returns the diff;
        # the decorator scopes the auth grant to exactly those
        # books (SpiceDB ``ImportBulkRelationships`` is idempotent
        # on the same triple so re-granting is a no-op anyway,
        # but skipping the diff keeps the audit log quiet).
        result = await self._inner.set_books_of(
            shelf_id, book_ids, user_ctx=user_ctx,
        )
        await self._write_user_edges(
            op="set_books_of",
            result=result,
            user_ctx=user_ctx,
            call_kwargs={"shelf_id": str(shelf_id)},
        )
        return result

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
        await self._inner.add_book(
            shelf_id, book_id, user_ctx=user_ctx,
        )
        await self._write_user_edges(
            op="add_book",
            result=None,
            user_ctx=user_ctx,
            call_kwargs={
                "shelf_id": str(shelf_id),
                "book_id": str(book_id),
            },
        )

    async def remove_book(
        self,
        shelf_id: str,
        book_id: str,
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> None:
        # Intentionally no SpiceDB grant: removal should not
        # revoke the user's owner / admin edges on the
        # underlying book.  Accept ``user_ctx`` for symmetry
        # with ``add_book``.
        await self._inner.remove_book(
            shelf_id, book_id, user_ctx=user_ctx,
        )

    # ---- permission policy ---------------------------------------------

    async def _write_user_edges(
        self,
        *,
        op: str,
        result: Any,
        user_ctx: Optional[UserContextABC],
        call_kwargs: Dict[str, Any],
    ) -> None:
        """Grant the right SpiceDB edges for ``op`` and ``user_ctx``.

        Behaviour:

        * ``user_ctx is None`` -- no-op.  The caller is
          acting as the system (e.g. a fixture-only test, a
          migration running on the user's behalf) and the
          policy is to skip the grant.
        * ``user_ctx`` is supplied but ``permission_repo``
          was not injected -- **raise** :class:`RuntimeError`.
          The caller explicitly declared a caller identity,
          so silently dropping the grant would be a security
          bug.  Failing loud is the only safe default.
        * ``_edges_for_op`` returns an empty list -- no-op
          (e.g. ``remove_book``, ``update_shelf``).
        * ``permission_repo.insert(...)`` raises -- swallow
          with a warning.  The underlying Postgres write
          already succeeded; a flaky SpiceDB should not roll
          back a successful m2m insert.
        """
        if user_ctx is None:
            return
        if self._permission_repo is None:
            raise RuntimeError(
                f"SpicedbShelfRepoDecorator.{op}() was called with a "
                f"non-None user_ctx (user_id={user_ctx.user_id!r}) but "
                f"the decorator was constructed without a permission_repo. "
                f"Wire a PermissionRepoABC into the decorator's "
                f"constructor so the SpiceDB edges for the caller can "
                f"be granted, or omit user_ctx when calling this op."
            )
        user_id = str(user_ctx.user_id)
        relationships = self._edges_for_op(op, result, user_id, call_kwargs)
        if not relationships:
            return
        try:
            await self._permission_repo.insert(relationships)
        except Exception as exc:  # noqa: BLE001 -- best-effort auth
            self.log.warning(
                f"shelf repo auth grant failed for op={op!r} "
                f"user={user_id!r}: {type(exc).__name__}: {exc}"
            )

    @staticmethod
    def _edges_for_op(
        op: str,
        result: Any,
        user_id: str,
        call_kwargs: Dict[str, Any],
    ) -> List[Relationship]:
        """Compute the SpiceDB edges for ``op``.

        Centralising the policy here keeps the class
        auditable: one method, one dict, every auth edge
        this class writes.

        * ``insert_shelf`` -- ``shelf#owner`` +
          ``shelf#admin`` on the new shelf.
        * ``add_book`` -- ``directory#owner`` +
          ``directory#admin`` on the bound book.
        * ``set_books_of`` -- ``directory#owner`` +
          ``directory#admin`` on each *newly added* book
          (the diff vs. the previous binding set).
        * ``remove_book`` -- intentionally empty; detaching a
          book from a shelf must not revoke the user's owner /
          admin edges on the underlying book.
        """
        if op == "insert_shelf":
            shelf_id = (
                str(result.id) if getattr(result, "id", None) is not None
                else None
            )
            return _shelf_owner_admin(shelf_id, user_id) if shelf_id else []
        if op == "add_book":
            book_id = str(call_kwargs.get("book_id", ""))
            return _book_owner_admin(book_id, user_id) if book_id else []
        if op == "set_books_of":
            # The inner repo returns the list of newly added
            # book ids (the diff vs. the previous binding set).
            # Grant one ``directory#owner`` + ``directory#admin``
            # pair per new book.
            new_books = result if isinstance(result, list) else []
            edges: List[Relationship] = []
            for book_id in new_books:
                edges.extend(_book_owner_admin(str(book_id), user_id))
            return edges
        return []


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
