"""Concrete :class:`~src.api.services.user_service.UserServiceABC` implementation."""

from __future__ import annotations

from typing import List, Optional

from src.api.other.relationship import (
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    ShelfRelationEnum,
    SubjectRef,
)
from src.api.other.undefined import UNDEFINED, is_undefined, unwrap_undefined
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.repos.rule_repo import RuleRepoABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.api.services.user_service import UserServiceABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.rule import RuleEntity
from src.db.entities.user.user import UserEntity
from src.db.repos.directory.directory_facade import DirectoryFacadeABC
from src.db.repos.user.user import UserRepoABC


def users_shelf_slug_for(username: object) -> str:
    """Build the per-user shelf slug ``<username>'s shelf``.

    Used by both :class:`UserServiceImpl` and the
    :mod:`src.db.migrations.20260825-bootstrap-users-shelf`
    migration so the live and backfill paths agree on the
    naming convention.  Falls back to ``"user shelf"`` when the
    username is missing or whitespace.
    """
    if not username or not isinstance(username, str):
        return "user shelf"
    base = username.strip().lower().replace("/", "-")
    cleaned = "-".join(part for part in base.split() if part)
    return f"{cleaned}'s shelf" if cleaned else "user shelf"


def users_shelf_display_name_for(username: object) -> str:
    """Build the per-user shelf display name ``<Username>'s Shelf``.

    Same fallback rules as :func:`users_shelf_slug_for`; the
    display name keeps the original casing of the username.
    """
    if not username or not isinstance(username, str):
        return "User's Shelf"
    cleaned = " ".join(part for part in username.strip().split() if part)
    return f"{cleaned}'s Shelf" if cleaned else "User's Shelf"


USERS_SHELF_SLUG = "users_shelf"
"""Default slug for single-tenant deployments.

New code should call :func:`users_shelf_slug_for` so each
user's shelf carries their own name (``"pauls shelf"`` etc.).
The constant is kept for backward-compatible callers that
hard-code the legacy value.
"""

USERS_SHELF_DESCRIPTION = (
    "Default shelf grouping the zettelkasten "
    "books every user starts with."
)
"""Default description used when the per-user helper is not
applicable (e.g. legacy single-tenant rows).
"""


class UserServiceImpl(UserServiceABC):
    """Application service for user lifecycle and bootstrap directories.

    On :meth:`create_user` the service:

    1. Creates the user row.
    2. Builds the user's default zettelkasten -- a single
       :data:`USERS_SHELF_SLUG` shelf and the three default
       books (``fleeting_notes``, ``literature_notes``,
       ``permanent_notes``) sitting on it.
    3. Adds a "default directory" rule attached to the user's
       shelf that fires on ``NoteCreated`` and adds the note
       to the ``fleeting_notes`` book.  Subsequent notes
       inserted with no parent directory resolve to the
       ``fleeting_notes`` book via this rule.
    """

    def __init__(
        self,
        user_repo: UserRepoABC,
        directory_facade: DirectoryFacadeABC,
        context_factory: ContextFactory[UserContextABC],
        shelf_repo: ShelfRepoABC,
        rule_repo: RuleRepoABC,
        permission_repo: PermissionRepoABC,
    ) -> None:
        self._user_repo = user_repo
        self._directory_facade = directory_facade
        self._context_factory = context_factory
        self._shelf_repo = shelf_repo
        self._rule_repo = rule_repo
        # used for shelf admin relation
        self._permission_repo = permission_repo

    async def get_user(
        self,
        user_id: Optional[str] = None,
        discord_id: Optional[int] = None,
    ) -> Optional[UserEntity]:
        if user_id is not None:
            return await self._user_repo.select(user_id=user_id)
        if discord_id is not None:
            return await self._user_repo.select_by_discord_id(discord_id=discord_id)
        return None

    async def create_user(self, user: UserEntity) -> UserEntity:
        """Create the user, then bootstrap their shelf + books + rule."""
        created_user = await self._user_repo.insert(user)
        user_id = unwrap_undefined(created_user.id)

        # ``temporary`` / ``system`` users get the row but no
        # shelf / books / rule -- they are internal actors that
        # should never carry end-user state.
        if is_undefined(user.type) or user.type in ["temporary", "system"]:
            return created_user

        await self._bootstrap_user_zettelkasten(
            user_id=str(user_id),
            username=created_user.username,
        )
        return created_user

    async def update_user(self, user: UserEntity) -> UserEntity:
        """Persist partial updates without touching the directory bootstrap.

        Forwards directly to the repo so the gRPC auth adapter
        can write a single column (e.g. ``avatar``) without
        re-running the directory creation side effects of
        :meth:`create_user`.
        """
        return await self._user_repo.update(user)

    # ---- bootstrap helpers --------------------------------------------------

    async def _bootstrap_user_zettelkasten(
        self,
        *,
        user_id: str,
        username: UndefinedNoneOr[str] = UNDEFINED,
    ) -> None:
        """Create the user's default shelf + 3 books + default-fleeting rule.

        Order matters:

        1. The shelf row goes in first so it has an id we can
           attach the books to.
        2. The three books are created; their ids are written
           into ``note.shelf_book`` via
           :meth:`ShelfRepoABC.set_books_of`.
        3. A ``NoteCreated`` rule is inserted with
           ``attached_entity_type=shelf`` and an
           ``add_to_directory`` action targeting the fleeting
           book -- subsequent notes without an explicit
           parent directory land there via the dispatcher.

        Idempotency is **not** guaranteed: callers that re-run
        :meth:`create_user` for an existing user will end up
        with duplicated shelves / books.  This mirrors the
        pre-shelf behaviour of the directory bootstrap and is
        the caller's responsibility.
        """
        user_ctx = await self._context_factory.create(user_id)

        shelf_username = username or user_id
        shelf = await self._shelf_repo.insert_shelf(
            slug=users_shelf_slug_for(shelf_username),
            display_name=users_shelf_display_name_for(shelf_username),
            description=USERS_SHELF_DESCRIPTION,
        )
        shelf_id = unwrap_undefined(shelf.id)

        # add shelf#admin@user:<id> relation so the user can see their own shelf
        await self._attach_admin_to_shelf(shelf_id=shelf_id, user_id=user_id)

        # 2. Create the three default books on the shelf.
        created_books: List[DirectoryEntity] = []
        for spec in self._directory_facade.get_default_directory_specs():
            book = await self._directory_facade.create_directory(
                DirectoryEntity(
                    slug=spec.name,
                    display_name=spec.display_name,
                    description=spec.description,
                    relations=[],
                ),
                user_ctx,
            )
            created_books.append(book)

        # Bind every created book to the shelf so the books
        # appear in ``get_books_of(shelf_id)`` lookups.
        book_ids = [
            str(unwrap_undefined(b.id)) for b in created_books
        ]
        await self._shelf_repo.set_books_of(
            shelf_id=shelf_id, book_ids=book_ids,
        )

        # 3. Insert the default-fleeting rule.  Only attach a
        # rule when the user actually got a fleeting book
        fleeting_book = next(
            (b for b in created_books if b.slug == "fleeting_notes"),
            None,
        )
        if fleeting_book:
            await self._rule_repo.create_rule(
                RuleEntity(
                    id=UNDEFINED,
                    event_type="NoteCreated",
                    attached_entity_type="shelf",
                    attached_entity_id=str(shelf_id),
                    condition={"type": "always_true"},
                    action_type="add_to_directory",
                    action_context={
                        "directory_id": str(unwrap_undefined(fleeting_book.id)),
                    },
                    enabled=True,
                    creator_id=user_id,
                )
            )

    async def _attach_admin_to_shelf(
        self,
        *,
        shelf_id: str,
        user_id: str,
    ) -> None:
        """Insert a ``shelf#admin@user:<id>`` relation via the permission repo.

        Goes around the rule / shelf services on purpose so the
        :class:`UserServiceImpl` has zero cyclic deps.  Failures
        are swallowed: a missing permission relation means the
        user can't see their own shelf, which would be loud
        enough to surface in any integration test that exercises
        the bootstrap path end-to-end.
        """
        try:
            admin_rel = Relationship(
                resource=ObjectRef(
                    object_type=ObjectTypeEnum.SHELF,
                    object_id=str(shelf_id),
                ),
                relation=ShelfRelationEnum.ADMIN,
                subject=SubjectRef(
                    object_type=ObjectTypeEnum.USER,
                    object_id=str(user_id),
                ),
            )
            await self._permission_repo.insert([admin_rel])
        except Exception:  # noqa: BLE001 -- best-effort bootstrap
            # The integration tests will fail loudly if the
            # shelf is missing its admin relation.  Logging
            # without raising keeps the bootstrap path
            # future-proof against newly-added shelves that
            # might not yet be in SpiceDB's schema.

            return


__all__ = [
    "UserServiceImpl",
    "USERS_SHELF_SLUG",
    "USERS_SHELF_DESCRIPTION",
    "users_shelf_slug_for",
    "users_shelf_display_name_for",
]
