from dataclasses import replace
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.relationship import ObjectRef, Relationship, SubjectRef
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.repos.directory_repo import DirectoryChildType, DirectoryHierarchyType, DirectoryParentType
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.user.user import UserEntity
from src.api.other.relationship import DirectoryRelationEnum, ObjectTypeEnum
from src.api.repos.user_repo import UserRepoABC
from src.services.shelf_service import ShelfServiceImpl
from src.services.user_service import UserServiceImpl
from tests.doubles import InMemoryPermissionRepo
from tests.doubles import InMemoryRuleRepo
from tests.doubles import InMemoryShelfRepo
from tests.doubles import _DummyUserContext as UserContext
from typing import Dict, List, Optional, Tuple

from tests.doubles.fakes.user_service import _InMemoryUserRepo, _InMemoryDirectoryRepo, _InMemoryContextFactory








def _make_test_user() -> UserEntity:
    return UserEntity(
        discord_id=123456789,
        avatar="avatar.png",
        username="paul",
        discriminator="0001",
        email="paul@example.com",
        type="human",
    )


def _make_service(
    user_repo,
    directory_repo,
    shelf_repo=None,
    rule_repo=None,
    permission_repo=None,
) -> UserServiceImpl:
    """Build a UserServiceImpl with sensible in-memory defaults.

    Constructs a real ShelfServiceImpl from the in-memory fakes
    so the user-service delegates shelf/rule work through the
    shelf service (matching production wiring).
    """
    shelf_repo = shelf_repo if shelf_repo is not None else InMemoryShelfRepo()
    rule_repo = rule_repo if rule_repo is not None else InMemoryRuleRepo()
    permission_repo = (
        permission_repo
        if permission_repo is not None
        else InMemoryPermissionRepo()
    )
    shelf_service = ShelfServiceImpl(
        shelf_repo=shelf_repo,
        permission_repo=permission_repo,
        directory_facade=directory_repo,
        rule_repo=rule_repo,
    )
    return UserServiceImpl(
        user_repo=user_repo,
        directory_facade=directory_repo,
        context_factory=_InMemoryContextFactory(),
        shelf_service=shelf_service,
    )


async def test_create_user_creates_default_zettelkasten_directories() -> None:
    user_repo = _InMemoryUserRepo()
    directory_repo = _InMemoryDirectoryRepo()
    service = _make_service(user_repo, directory_repo)

    created_user = await service.create_user(_make_test_user())

    assert created_user.id is not None
    assert len(directory_repo.created) == 3

    assert [d.slug for d in directory_repo.created] == [
        "fleeting_notes",
        "literature_notes",
        "permanent_notes",
    ]
    assert [d.display_name for d in directory_repo.created] == [
        "Fleeting Notes",
        "Literature Notes",
        "Permanent Notes",
    ]
    assert all(isinstance(d.description, str) and "zettelkasten" in d.description.lower() for d in directory_repo.created)


async def test_create_user_assigns_admin_relation_to_bootstrap_directories() -> None:
    user_repo = _InMemoryUserRepo()
    directory_repo = _InMemoryDirectoryRepo()
    service = _make_service(user_repo, directory_repo)

    created_user = await service.create_user(_make_test_user())

    for directory in directory_repo.created:
        assert isinstance(directory.relations, list)
        assert len(directory.relations) == 1
        rel = directory.relations[0]
        assert rel.relation == DirectoryRelationEnum.ADMIN
        assert rel.subject.object_type == ObjectTypeEnum.USER
        assert rel.subject.object_id == created_user.id


async def test_get_user_resolves_by_id_and_discord_id() -> None:
    user_repo = _InMemoryUserRepo()
    directory_repo = _InMemoryDirectoryRepo()
    service = _make_service(user_repo, directory_repo)

    created_user = await service.create_user(_make_test_user())

    by_id = await service.get_user(user_id=created_user.id)
    by_discord = await service.get_user(discord_id=created_user.discord_id)
    by_none = await service.get_user()

    assert by_id == created_user
    assert by_discord == created_user
    assert by_none is None


# ---------------------------------------------------------------------------
# Shelf bootstrap tests
# ---------------------------------------------------------------------------


async def test_create_user_creates_a_users_shelf() -> None:
    """``create_user`` creates a single ``users_shelf`` row per human user."""
    shelf_repo = InMemoryShelfRepo()
    user_repo = _InMemoryUserRepo()
    directory_repo = _InMemoryDirectoryRepo()
    service = _make_service(
        user_repo=user_repo,
        directory_repo=directory_repo,
        shelf_repo=shelf_repo,
    )

    created_user = await service.create_user(_make_test_user())

    shelves = await shelf_repo.fetch_shelves_by_ids(
        [str(s.id) for s in shelf_repo._shelves.values() if s.id]
    )
    assert len(shelves) == 1, "exactly one shelf must be created"
    shelf = shelves[0]
    # Slug is ``<username>'s shelf``; the test user has
    # username="paul".
    assert shelf.slug == "paul's shelf"
    assert shelf.display_name == "paul's Shelf"
    # The shelf id must be unique per user; this assertion is
    # redundant with the count check but documents the
    # behaviour.
    assert shelf.id not in (None, UNDEFINED)


async def test_create_user_attaches_three_books_to_shelf() -> None:
    """The 3 bootstrap books (fleeting/literature/permanent) bind to the shelf."""
    shelf_repo = InMemoryShelfRepo()
    user_repo = _InMemoryUserRepo()
    directory_repo = _InMemoryDirectoryRepo()
    service = _make_service(
        user_repo=user_repo,
        directory_repo=directory_repo,
        shelf_repo=shelf_repo,
    )

    created_user = await service.create_user(_make_test_user())

    shelves = await shelf_repo.fetch_shelves_by_ids(
        [str(s.id) for s in shelf_repo._shelves.values() if s.id],
        include_books=True,
    )
    shelf = shelves[0]
    assert len(shelf.book_ids) == 3, (
        f"expected 3 books on the shelf, got {len(shelf.book_ids)}: "
        f"{shelf.book_ids!r}"
    )
    # All 3 books must also exist in the directory repo.
    book_slugs = sorted(
        d.slug for d in directory_repo.created if d.slug
    )
    assert book_slugs == [
        "fleeting_notes", "literature_notes", "permanent_notes"
    ]
    # The shelf's book_ids must match the directory ids.
    assert sorted(str(d.id) for d in directory_repo.created) == sorted(
        shelf.book_ids
    )


async def test_create_user_creates_default_fleeting_rule_attached_to_shelf() -> None:
    """A default-fleeting rule is inserted and attached to the user's shelf."""
    shelf_repo = InMemoryShelfRepo()
    user_repo = _InMemoryUserRepo()
    directory_repo = _InMemoryDirectoryRepo()
    rule_repo = InMemoryRuleRepo()
    service = _make_service(
        user_repo=user_repo,
        directory_repo=directory_repo,
        shelf_repo=shelf_repo,
        rule_repo=rule_repo,
    )

    created_user = await service.create_user(_make_test_user())

    # Exactly one rule was inserted.
    rules = await rule_repo.list_rules()
    assert len(rules) == 1, f"expected 1 rule, got {len(rules)}"
    rule = rules[0]
    assert rule.event_type == "NoteCreated"
    assert rule.attached_entity_type == "shelf"
    assert rule.action_type == "add_to_directory"
    assert rule.enabled is True
    assert rule.creator_id == created_user.id

    # The rule is attached to the same shelf we created.
    shelf_id = next(iter(shelf_repo._shelves))
    assert rule.attached_entity_id == shelf_id

    # The action_context points at the fleeting book id.
    fleeting_book = next(
        d for d in directory_repo.created if d.slug == "fleeting_notes"
    )
    assert rule.action_context == {
        "directory_id": str(fleeting_book.id)
    }


async def test_create_user_skips_shelf_for_temporary_user() -> None:
    """``temporary`` users get a row but no shelf/books/rule."""
    shelf_repo = InMemoryShelfRepo()
    user_repo = _InMemoryUserRepo()
    directory_repo = _InMemoryDirectoryRepo()
    rule_repo = InMemoryRuleRepo()
    service = _make_service(
        user_repo=user_repo,
        directory_repo=directory_repo,
        shelf_repo=shelf_repo,
        rule_repo=rule_repo,
    )

    tmp = _make_test_user()
    tmp = type(tmp)(
        **{**tmp.__dict__, "type": "temporary"}
    )
    await service.create_user(tmp)

    assert await shelf_repo.fetch_shelves_by_ids(
        [str(s.id) for s in shelf_repo._shelves.values() if s.id]
    ) == []
    assert directory_repo.created == []
    assert await rule_repo.list_rules() == []


async def test_create_user_skips_shelf_for_system_user() -> None:
    """``system`` users get a row but no shelf/books/rule."""
    shelf_repo = InMemoryShelfRepo()
    user_repo = _InMemoryUserRepo()
    directory_repo = _InMemoryDirectoryRepo()
    rule_repo = InMemoryRuleRepo()
    service = _make_service(
        user_repo=user_repo,
        directory_repo=directory_repo,
        shelf_repo=shelf_repo,
        rule_repo=rule_repo,
    )

    sys_user = _make_test_user()
    sys_user = type(sys_user)(
        **{**sys_user.__dict__, "type": "system"}
    )
    await service.create_user(sys_user)

    assert await shelf_repo.fetch_shelves_by_ids(
        [str(s.id) for s in shelf_repo._shelves.values() if s.id]
    ) == []
    assert directory_repo.created == []
    assert await rule_repo.list_rules() == []


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------


def _seed_users() -> tuple[_InMemoryUserRepo, list[UserEntity]]:
    """Insert a small fixture set directly through the repo.

    Bypasses ``create_user`` so the search tests don't pay for the
    shelf/directory bootstrap.
    """
    repo = _InMemoryUserRepo()
    users = [
        UserEntity(
            discord_id=1, username="paul", display_name="Paul",
            email="paul@example.com", type="human",
        ),
        UserEntity(
            discord_id=2, username="paula", display_name="Paula",
            email="paula@example.com", type="human",
        ),
        UserEntity(
            discord_id=3, username="bob", display_name="Bob",
            email="bob@example.com", type="human",
        ),
    ]
    return repo, users


async def test_search_users_fuzzy_matches_username() -> None:
    """A partial name returns the users whose username contains it."""
    user_repo, users = _seed_users()
    for user in users:
        await user_repo.insert(user)
    service = _make_service(user_repo, _InMemoryDirectoryRepo())

    results = await service.search_users("pau", UserContext(user_id="u-0"))

    assert [u.username for u in results] == ["paul", "paula"]


async def test_search_users_matches_partial_id() -> None:
    """A query that looks like (part of) an id returns each user whose id contains it."""
    user_repo, users = _seed_users()
    inserted = [await user_repo.insert(user) for user in users]
    service = _make_service(user_repo, _InMemoryDirectoryRepo())

    results = await service.search_users(
        str(inserted[1].id), UserContext(user_id="u-0")
    )

    assert [u.id for u in results] == [inserted[1].id]


async def test_search_users_email_matches_exactly() -> None:
    """An email-shaped query only returns the exact address, never partials."""
    user_repo, users = _seed_users()
    for user in users:
        await user_repo.insert(user)
    service = _make_service(user_repo, _InMemoryDirectoryRepo())

    exact = await service.search_users(
        "paula@example.com", UserContext(user_id="u-0")
    )
    partial = await service.search_users(
        "paula@", UserContext(user_id="u-0")
    )

    assert [u.username for u in exact] == ["paula"]
    assert partial == []


async def test_search_users_returns_at_most_ten_results() -> None:
    """The service caps the result set at the first 10 matches."""
    user_repo = _InMemoryUserRepo()
    for i in range(15):
        await user_repo.insert(
            UserEntity(
                discord_id=i,
                username=f"user{i:02d}",
                email=f"user{i:02d}@example.com",
                type="human",
            )
        )
    service = _make_service(user_repo, _InMemoryDirectoryRepo())

    results = await service.search_users("user", UserContext(user_id="u-0"))

    assert len(results) == 10


async def test_search_users_blank_query_returns_nothing() -> None:
    """An empty or whitespace-only query never matches."""
    user_repo, users = _seed_users()
    for user in users:
        await user_repo.insert(user)
    service = _make_service(user_repo, _InMemoryDirectoryRepo())

    assert await service.search_users("", UserContext(user_id="u-0")) == []
    assert await service.search_users("   ", UserContext(user_id="u-0")) == []
