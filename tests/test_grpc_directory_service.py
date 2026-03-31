import logging
from typing import Dict, List, Optional, cast

import grpc
from grpc.aio import ServicerContext

from src.api.undefined import UNDEFINED
from src.db.entities.directory.directory import DirectoryEntity
from src.db.repos.directory.directory import DirectoryRepo
from src.grpc_mod.proto.note_pb2 import AlterDirectoryRequest, DeleteDirectoryRequest, GetDirectoriesRequest
from src.grpc_mod.service import GrpcDirectoryService


class _FakeContext:
    def __init__(self) -> None:
        self.code: Optional[grpc.StatusCode] = None
        self.details: Optional[str] = None

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


class _StubDirectoryRepo(DirectoryRepo):
    def __init__(self, user_to_ids: Dict[str, List[str]], by_id: Dict[str, DirectoryEntity]) -> None:
        self._user_to_ids = user_to_ids
        self._by_id = by_id
        self.last_user_id: Optional[str] = None
        self.deleted_ids: List[str] = []
        self.last_update_entity: Optional[DirectoryEntity] = None

    async def create_directory(self, entity: DirectoryEntity) -> DirectoryEntity:
        raise NotImplementedError()

    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        return self._by_id.get(id)

    async def update_directory(self, entity: DirectoryEntity) -> Optional[DirectoryEntity]:
        self.last_update_entity = entity
        if entity.id not in self._by_id:
            return None

        current = self._by_id[str(entity.id)]
        updated = DirectoryEntity(
            id=current.id,
            name=current.name if entity.name is UNDEFINED else entity.name,
            display_name=current.display_name if entity.display_name is UNDEFINED else entity.display_name,
            description=current.description if entity.description is UNDEFINED else entity.description,
            image_url=current.image_url if entity.image_url is UNDEFINED else entity.image_url,
            parent_id=current.parent_id if entity.parent_id is UNDEFINED else entity.parent_id,
            relations=current.relations,
        )
        self._by_id[str(entity.id)] = updated
        return updated

    async def list_user_directory_ids(self, user) -> List[str]:
        self.last_user_id = user.user_id
        return list(self._user_to_ids.get(user.user_id, []))

    async def fetch_all_directories(self) -> List[DirectoryEntity]:
        raise AssertionError("GetDirectories should not use fetch_all_directories")

    async def list_note_directory_ids(self, note_id: str) -> List[str]:
        raise NotImplementedError()

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        directory_id = str(entity.id)
        self.deleted_ids.append(directory_id)
        return directory_id in self._by_id


def _log_provider(*_args, **_kwargs):
    return logging.getLogger("test.grpc.directory")


async def test_get_directories_requires_user_id() -> None:
    repo = _StubDirectoryRepo(user_to_ids={}, by_id={})
    service = GrpcDirectoryService(directory_repo=repo, log=_log_provider)
    context = _FakeContext()

    request = GetDirectoriesRequest(user_id="")
    result = [directory async for directory in service.GetDirectories(request, cast(ServicerContext, context))]

    assert result == []
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "user_id is required"


async def test_get_directories_returns_only_user_visible_directories() -> None:
    dir_1 = DirectoryEntity(id="dir-1", name="one", parent_id="parent-a", relations=[])
    dir_2 = DirectoryEntity(id="dir-2", name="two", parent_id="parent-a", relations=[])
    dir_3 = DirectoryEntity(id="dir-3", name="three", parent_id="parent-b", relations=[])

    repo = _StubDirectoryRepo(
        user_to_ids={"user-1": ["dir-1", "dir-2", "dir-3"]},
        by_id={"dir-1": dir_1, "dir-2": dir_2, "dir-3": dir_3},
    )
    service = GrpcDirectoryService(directory_repo=repo, log=_log_provider)
    context = _FakeContext()

    request = GetDirectoriesRequest(user_id="user-1", parent_id="parent-a", limit=1, offset=1)
    result = [directory async for directory in service.GetDirectories(request, cast(ServicerContext, context))]

    assert repo.last_user_id == "user-1"
    assert context.code is None
    assert [directory.id for directory in result] == ["dir-2"]


async def test_get_directories_excludes_missing_directory_records() -> None:
    repo = _StubDirectoryRepo(
        user_to_ids={"user-1": ["dir-1", "dir-missing"]},
        by_id={"dir-1": DirectoryEntity(id="dir-1", name="one", parent_id=UNDEFINED, relations=[])},
    )
    service = GrpcDirectoryService(directory_repo=repo, log=_log_provider)
    context = _FakeContext()

    request = GetDirectoriesRequest(user_id="user-1")
    result = [directory async for directory in service.GetDirectories(request, cast(ServicerContext, context))]

    assert [directory.id for directory in result] == ["dir-1"]


async def test_delete_directory_requires_id() -> None:
    repo = _StubDirectoryRepo(user_to_ids={}, by_id={})
    service = GrpcDirectoryService(directory_repo=repo, log=_log_provider)
    context = _FakeContext()

    result = await service.DeleteDirectory(DeleteDirectoryRequest(id="", user_id="user-1"), cast(ServicerContext, context))

    assert result.id == ""
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "id is required"


async def test_delete_directory_returns_not_found_when_repo_reports_no_delete() -> None:
    repo = _StubDirectoryRepo(user_to_ids={}, by_id={})
    service = GrpcDirectoryService(directory_repo=repo, log=_log_provider)
    context = _FakeContext()

    result = await service.DeleteDirectory(DeleteDirectoryRequest(id="dir-missing", user_id="user-1"), cast(ServicerContext, context))

    assert repo.deleted_ids == ["dir-missing"]
    assert result.id == ""
    assert context.code == grpc.StatusCode.NOT_FOUND
    assert context.details == "Directory not found"


async def test_delete_directory_returns_deleted_id_on_success() -> None:
    repo = _StubDirectoryRepo(
        user_to_ids={},
        by_id={"dir-1": DirectoryEntity(id="dir-1", name="one", relations=[])},
    )
    service = GrpcDirectoryService(directory_repo=repo, log=_log_provider)
    context = _FakeContext()

    result = await service.DeleteDirectory(DeleteDirectoryRequest(id="dir-1", user_id="user-1"), cast(ServicerContext, context))

    assert repo.deleted_ids == ["dir-1"]
    assert result.id == "dir-1"
    assert context.code is None


async def test_patch_directory_requires_id() -> None:
    repo = _StubDirectoryRepo(user_to_ids={}, by_id={})
    service = GrpcDirectoryService(directory_repo=repo, log=_log_provider)
    context = _FakeContext()

    result = await service.PatchDirectory(
        AlterDirectoryRequest(id="", user_id="user-1", name="new-name"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "id is required"


async def test_patch_directory_returns_not_found_for_missing_directory() -> None:
    repo = _StubDirectoryRepo(user_to_ids={}, by_id={})
    service = GrpcDirectoryService(directory_repo=repo, log=_log_provider)
    context = _FakeContext()

    result = await service.PatchDirectory(
        AlterDirectoryRequest(id="dir-missing", user_id="user-1", name="new-name"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.NOT_FOUND
    assert context.details == "Directory not found"


async def test_patch_directory_updates_requested_fields() -> None:
    repo = _StubDirectoryRepo(
        user_to_ids={},
        by_id={
            "dir-1": DirectoryEntity(
                id="dir-1",
                name="old-name",
                display_name="Old Display",
                description="Old Description",
                image_url="old-url",
                parent_id="old-parent",
                relations=[],
            )
        },
    )
    service = GrpcDirectoryService(directory_repo=repo, log=_log_provider)
    context = _FakeContext()

    result = await service.PatchDirectory(
        AlterDirectoryRequest(
            id="dir-1",
            user_id="user-1",
            name="new-name",
            description="new-description",
            parent_id="new-parent",
        ),
        cast(ServicerContext, context),
    )

    assert context.code is None
    assert repo.last_update_entity is not None
    assert repo.last_update_entity.id == "dir-1"
    assert repo.last_update_entity.name == "new-name"
    assert repo.last_update_entity.description == "new-description"
    assert repo.last_update_entity.parent_id == "new-parent"
    assert repo.last_update_entity.display_name is UNDEFINED
    assert repo.last_update_entity.image_url is UNDEFINED
    assert result.id == "dir-1"
    assert result.name == "new-name"
    assert result.description == "new-description"
    assert result.parent_id == "new-parent"
