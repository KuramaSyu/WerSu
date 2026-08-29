from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from tests.stubs.in_memory_shelf_repo import InMemoryShelfRepo
from tests.stubs.in_memory_rule_repo import InMemoryRuleRepo
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.user_context import _UserContext as UserContext
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import ContextFactory, UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.user.user import UserEntity
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
    DirectoryParentType,
)
from src.api.other.relationship import ObjectRef, Relationship, SubjectRef
from src.db.repos.note.permission import DirectoryRelationEnum, ObjectTypeEnum
from src.db.repos.user.user import UserRepoABC
from src.services.shelf_service import ShelfServiceImpl
from src.services.user_service import UserServiceImpl


class _InMemoryUserRepo(UserRepoABC):
    def __init__(self) -> None:
        self._by_id: Dict[str, UserEntity] = {}
        self._by_discord_id: Dict[int, str] = {}
        self._counter = 1

    async def insert(self, user: UserEntity) -> UserEntity:
        user_id = f"user-{self._counter}"
        self._counter += 1
        created = replace(user, id=user_id)
        self._by_id[user_id] = created
        self._by_discord_id[created.discord_id] = user_id
        return created

    async def update(self, user: UserEntity) -> UserEntity:
        if user.id is None:
            raise ValueError("User ID is required for update operation")
        self._by_id[user.id] = user
        self._by_discord_id[user.discord_id] = user.id
        return user

    async def upsert(self, user: UserEntity) -> UserEntity:
        existing = await self.select_by_discord_id(user.discord_id)
        if existing is None:
            return await self.insert(user)
        updated = replace(user, id=existing.id)
        return await self.update(updated)

    async def select(self, user_id: str) -> Optional[UserEntity]:
        return self._by_id.get(user_id)

    async def select_by_discord_id(self, discord_id: int) -> Optional[UserEntity]:
        user_id = self._by_discord_id.get(discord_id)
        if user_id is None:
            return None
        return self._by_id[user_id]

    async def delete(self, user_id: str) -> bool:
        user = self._by_id.pop(user_id, None)
        if user is None:
            return False
        self._by_discord_id.pop(user.discord_id, None)
        return True


class _InMemoryDirectoryRepo(DirectoryFacadeABC):
    def __init__(self) -> None:
        self.created: List[DirectoryEntity] = []

    async def create_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: Optional[UserContextABC] = None,
    ) -> DirectoryEntity:
        created = replace(entity, id=f"dir-{len(self.created) + 1}")
        # Mirror production :class:`DirectoryFacadeImpl` behaviour:
        # always attach a `dir#admin@user` relation for the caller.
        if user_ctx is not None:
            admin_rel = Relationship(
                resource=ObjectRef(
                    object_type=ObjectTypeEnum.DIRECTORY,
                    object_id=str(created.id),
                ),
                relation=DirectoryRelationEnum.ADMIN,
                subject=SubjectRef(
                    object_type=ObjectTypeEnum.USER,
                    object_id=str(user_ctx.user_id),
                ),
            )
            existing = list(created.relations or [])
            existing.append(admin_rel)
            created = replace(created, relations=existing)
        self.created.append(created)
        return created

    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        for directory in self.created:
            if directory.id == id:
                return directory
        return None

    async def update_directory(self, entity: DirectoryEntity) -> Optional[DirectoryEntity]:
        return entity

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        return [str(directory.id) for directory in self.created if directory.id is not UNDEFINED]

    async def fetch_all_directories(self) -> List[DirectoryEntity]:
        return list(self.created)

    async def fetch_directories_by_ids(
        self, ids: List[str],
    ) -> List[DirectoryEntity]:
        # Mirror :meth:`fetch_directory`: linear scan over the
        # in-memory ``created`` list.  Missing ids silently drop.
        out: List[DirectoryEntity] = []
        for did in ids:
            for d in self.created:
                if str(d.id) == str(did):
                    out.append(d)
                    break
        return out

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        return False

    async def resolve_files_of_directory(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
    ) -> List[str]:
        return []

    async def resolve_subtree(
        self,
        directory_id: str,
        max_depth: int = 10,
    ) -> Tuple[List[str], List[str]]:
        return ([], [directory_id])

    # ---- DirectoryHelperMixin: hierarchy helpers (no-op stubs) ------

    async def set_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
    ) -> None:
        return None

    async def get_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
    ) -> List[str]:
        return []

    async def get_children_of(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> List[str]:
        return []

    async def get_children_for(
        self,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> Dict[str, List[str]]:
        return {str(d): [] for d in parent_ids}

    async def get_parents_for(
        self,
        child_type: DirectoryChildType,
        child_ids: List[str],
        parent_type: DirectoryParentType,
    ) -> Dict[str, List[str]]:
        return {str(c): [] for c in child_ids}

    async def add_child_to(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        return None

    async def remove_child_from(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        return None


class _InMemoryContextFactory(ContextFactory[UserContextABC]):
    """In-memory ContextFactory used by unit tests."""

    async def create(self, user_id: str) -> UserContextABC:
        return UserContext(user_id=user_id)


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
    from tests.stubs.in_memory_rule_repo import InMemoryRuleRepo
    from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
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
