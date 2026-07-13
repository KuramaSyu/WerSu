from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from tests.stubs.user_context import _UserContext as UserContext
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import ContextFactory, UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.user.user import UserEntity
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.other.relationship import ObjectRef, Relationship, SubjectRef
from src.db.repos.note.permission import DirectoryRelationEnum, ObjectTypeEnum
from src.db.repos.user.user import UserRepoABC
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

    async def list_note_directory_ids(self, note_id: str) -> List[str]:
        return []

    async def add_note_to_directory(self, note_id: str, directory_id: str) -> None:
        """No-op recording stub."""
        return None

    async def remove_note_from_directory(self, note_id: str, directory_id: str) -> None:
        """No-op recording stub."""
        return None

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


def _make_service(user_repo, directory_repo) -> UserServiceImpl:
    return UserServiceImpl(
        user_repo=user_repo,
        directory_repo=directory_repo,
        context_factory=_InMemoryContextFactory(),
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
