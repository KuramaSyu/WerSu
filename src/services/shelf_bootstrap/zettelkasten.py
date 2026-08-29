"""Zettelkasten bootstrap recipe."""

from __future__ import annotations

from typing import Dict, List, Optional

from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.undefined import UNDEFINED, unwrap_undefined
from src.api.other.user_context import UserContextABC
from src.api.repos.rule_repo import RuleRepoABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.api.services.shelf_service import BootstrapResult
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.rule import RuleEntity
from src.db.entities.shelf import ShelfEntity

from src.services.shelf_bootstrap.strategy import ShelfBootstrapStrategy


_FLEETING_BOOK_SLUG = "fleeting_notes"


class ZettelkastenStrategy(ShelfBootstrapStrategy):
    """Bootstrap the three default books + the fleeting routing rule.

    Idempotent on three independent probes:

    1. Books: only default slugs missing from the shelf get created.
    2. Bindings: only newly-created default books are bound; pre-existing
       default and non-default bindings are preserved.
    3. Rule: inserted only when no NoteCreated rule already exists for the
       shelf and a fleeting book is reachable.

    SpiceDB ``shelf#owner`` / ``shelf#admin`` /
    ``directory#owner`` / ``directory#admin`` edges are
    granted automatically by the shelf repo's
    :func:`~src.db.repos.shelf.postgres.writes_user_permissions`
    decorator -- the strategy just forwards ``user_ctx`` to
    every ``shelf_repo.insert_shelf`` / ``add_book`` /
    ``set_books_of`` call it makes.
    """

    name: str = "zettelkasten"

    def __init__(
        self,
        *,
        shelf_repo: ShelfRepoABC,
        rule_repo: RuleRepoABC,
        directory_facade: DirectoryFacadeABC,
    ) -> None:
        self._shelf_repo = shelf_repo
        self._rule_repo = rule_repo
        self._directory_facade = directory_facade

    async def apply(
        self,
        *,
        shelf: ShelfEntity,
        owner_id: str,
        user_ctx: UserContextABC,
    ) -> BootstrapResult:
        shelf_id = str(unwrap_undefined(shelf.id))

        # Probe 1: which default slugs are already on the shelf?
        existing_book_ids = await self._shelf_repo.get_books_of(shelf_id)
        book_id_to_slug = await self._slugs_for(existing_book_ids)
        existing_slugs_to_book_id = {
            slug: bid for bid, slug in book_id_to_slug.items()
        }

        # Pass 1: create whichever default books are missing.
        created_books: List[DirectoryEntity] = []
        for spec in self._directory_facade.DEFAULT_DIRECTORY_SPECS:
            if spec.name in existing_slugs_to_book_id:
                continue
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

        # Pass 2: bind newly-created default books. add_book is
        # idempotent on note.shelf_book; pre-existing bindings stay.
        # Forward ``user_ctx`` so the
        # :func:`~src.db.repos.shelf.postgres.writes_user_permissions`
        # decorator grants ``directory#owner`` / ``directory#admin``
        # on the newly bound books for the caller -- matching
        # what ``create_directory`` already does.
        for spec in self._directory_facade.DEFAULT_DIRECTORY_SPECS:
            if spec.name in existing_slugs_to_book_id:
                continue
            for book in created_books:
                if book.slug == spec.name:
                    await self._shelf_repo.add_book(
                        shelf_id=shelf_id,
                        book_id=str(unwrap_undefined(book.id)),
                        user_ctx=user_ctx,
                    )
                    break

        # Probe 2 + Pass 3: fleeting rule.
        fleeting_id = existing_slugs_to_book_id.get(_FLEETING_BOOK_SLUG)
        if fleeting_id is None:
            for book in created_books:
                if book.slug == _FLEETING_BOOK_SLUG:
                    fleeting_id = str(unwrap_undefined(book.id))
                    break

        created_rule_id, rule_kept = await ensure_default_fleeting_rule(
            rule_repo=self._rule_repo,
            shelf_id=shelf_id,
            owner_id=owner_id,
            fleeting_directory_id=fleeting_id,
        )

        default_pre_existing = sum(
            1 for spec in self._directory_facade.DEFAULT_DIRECTORY_SPECS
            if spec.name in existing_slugs_to_book_id
        )
        return BootstrapResult(
            created_directory_ids=[
                str(unwrap_undefined(b.id)) for b in created_books
            ],
            created_rule_id=created_rule_id,
            description=(
                f"zettelkasten bootstrap on shelf {shelf_id}: "
                f"{len(created_books)} book(s) created, "
                f"{default_pre_existing} pre-existing, "
                f"rule {'kept' if rule_kept else 'created'}"
            ),
        )

    async def _slugs_for(self, book_ids: List[str]) -> Dict[str, str]:
        """Map book_id to slug for the given books; missing ids are dropped."""
        if not book_ids:
            return {}
        directories = await self._directory_facade.fetch_directories_by_ids(
            [str(b) for b in book_ids if b]
        )
        out: Dict[str, str] = {}
        for d in directories:
            if d.id is None or d.slug is None:
                continue
            out[str(unwrap_undefined(d.id))] = str(d.slug)
        return out


async def ensure_default_fleeting_rule(
    *,
    rule_repo: RuleRepoABC,
    shelf_id: str,
    owner_id: str,
    fleeting_directory_id: Optional[str] = None,
) -> tuple[Optional[str], bool]:
    """Ensure a single NoteCreated rule exists for the shelf.

    Returns (rule_id, kept): kept=True means a rule already existed.
    No-ops when fleeting_directory_id is None and no rule is present.
    Shared by ZettelkastenStrategy.apply and the bootstrap-users-shelf
    migration so both flows probe the same way.
    """
    existing = await rule_repo.list_rules(
        event_type="NoteCreated",
        attached_entity_type="shelf",
        attached_entity_id=shelf_id,
    )
    if existing:
        rid = existing[0].id
        if rid is None:
            return None, True
        return str(unwrap_undefined(rid)), True

    if fleeting_directory_id is None:
        return None, True

    created = await rule_repo.create_rule(
        RuleEntity(
            id=UNDEFINED,
            event_type="NoteCreated",
            attached_entity_type="shelf",
            attached_entity_id=shelf_id,
            condition={"type": "always_true"},
            action_type="add_to_directory",
            action_context={"directory_id": str(fleeting_directory_id)},
            enabled=True,
            creator_id=owner_id,
        )
    )
    return str(unwrap_undefined(created.id)), False


__all__ = [
    "ZettelkastenStrategy",
    "_FLEETING_BOOK_SLUG",
    "ensure_default_fleeting_rule",
]