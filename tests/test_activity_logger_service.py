"""Tests for :class:`DefaultActivityLoggerService`.

Uses an in-memory :class:`ActivityRepoABC` fake so each call lands on a
record-and-verify path -- no database, no asyncpg, fast feedback.
"""

from __future__ import annotations

import pytest

from src.api.activity import ActivityRepoABC
from src.api.activity_logger_service import (
    ActivityLoggerError,
    RoleChangeMetadata,
    RoleGrantMetadata,
    RoleRevokeMetadata,
)
from src.db.entities.activity import ActivityEntity
from src.services.activity_logger_service import DefaultActivityLoggerService
from tests.stubs.activity_repo import _FakeActivityRepo
from tests.stubs.user_context import _UserContext as _FakeUserContext


@pytest.fixture
def repo() -> _FakeActivityRepo:
    return _FakeActivityRepo()


@pytest.fixture
def repo() -> _FakeActivityRepo:
    return _FakeActivityRepo()


@pytest.fixture
def logger(repo: _FakeActivityRepo) -> DefaultActivityLoggerService:
    return DefaultActivityLoggerService(activity_repo=repo)


@pytest.fixture
def alice() -> _FakeUserContext:
    return _FakeUserContext(user_id="alice", accessed_as="user")


# Note-target methods


class TestNoteMethods:
    """Every ``note_*`` method records the right action + note_id."""

    @pytest.mark.asyncio
    async def test_note_viewed(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_viewed("n-1", alice)
        assert len(repo.added) == 1
        e = repo.added[0]
        assert e.action == "note_viewed"
        assert e.note_id == "n-1"
        assert e.directory_id is None
        assert e.role_id is None
        assert e.actor_id == "alice"
        assert e.accessed_as == "user"

    @pytest.mark.asyncio
    async def test_note_created(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_created("n-1", alice)
        assert repo.added[0].action == "note_created"
        assert repo.added[0].note_id == "n-1"

    @pytest.mark.asyncio
    async def test_note_edited(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_edited("n-1", alice, metadata={"from": "v1"})
        assert repo.added[0].action == "note_edited"
        assert repo.added[0].metadata == {"from": "v1"}

    @pytest.mark.asyncio
    async def test_note_deleted(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_deleted("n-1", alice)
        assert repo.added[0].action == "note_deleted"

    @pytest.mark.asyncio
    async def test_note_published(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_published("n-1", alice)
        assert repo.added[0].action == "note_published"

    @pytest.mark.asyncio
    async def test_note_shared(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_shared("n-1", alice)
        assert repo.added[0].action == "note_shared"

    @pytest.mark.asyncio
    async def test_note_restored(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_restored("n-1", alice)
        assert repo.added[0].action == "note_restored"

    @pytest.mark.asyncio
    async def test_note_archived(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_archived("n-1", alice)
        assert repo.added[0].action == "note_archived"

    @pytest.mark.asyncio
    async def test_note_version_restored_carries_version(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_version_restored("n-1", alice, version=7)
        e = repo.added[0]
        assert e.action == "note_version_restored"
        assert e.metadata == {"version": 7}

    @pytest.mark.asyncio
    async def test_note_version_restored_merges_extra_metadata(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_version_restored(
            "n-1", alice, version=7, metadata={"reason": "rollback"},
        )
        assert repo.added[0].metadata == {"version": 7, "reason": "rollback"}

    @pytest.mark.asyncio
    async def test_note_attachment_added_carries_attachment_id(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.note_attachment_added("n-1", alice, attachment_id="a-1")
        e = repo.added[0]
        assert e.action == "note_attachment_added"
        assert e.metadata == {"attachment_id": "a-1"}


# Directory-target methods


class TestDirectoryMethods:
    """Every ``directory_*`` method records the right action + directory_id."""

    @pytest.mark.asyncio
    async def test_directory_created(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.directory_created("d-1", alice)
        e = repo.added[0]
        assert e.action == "directory_created"
        assert e.directory_id == "d-1"
        assert e.note_id is None
        assert e.role_id is None

    @pytest.mark.asyncio
    async def test_directory_viewed(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.directory_viewed("d-1", alice)
        assert repo.added[0].action == "directory_viewed"

    @pytest.mark.asyncio
    async def test_directory_edited(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.directory_edited("d-1", alice)
        assert repo.added[0].action == "directory_edited"

    @pytest.mark.asyncio
    async def test_directory_deleted(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.directory_deleted("d-1", alice)
        assert repo.added[0].action == "directory_deleted"


# Role-target methods


class TestRoleMethods:
    """Role methods record role_id + serialized metadata dataclass."""

    @pytest.mark.asyncio
    async def test_role_granted_records_dataclass_as_dict(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.role_granted(
            alice,
            role_id="r-1",
            metadata=RoleGrantMetadata(subject_id="bob", role_name="writer"),
        )
        e = repo.added[0]
        assert e.action == "role_grant"
        assert e.role_id == "r-1"
        assert e.note_id is None
        assert e.directory_id is None
        assert e.metadata == {"subject_id": "bob", "role_name": "writer"}

    @pytest.mark.asyncio
    async def test_role_revoked(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.role_revoked(
            alice,
            role_id="r-1",
            metadata=RoleRevokeMetadata(subject_id="bob", role_name="writer"),
        )
        e = repo.added[0]
        assert e.action == "role_revoke"
        assert e.role_id == "r-1"
        assert e.metadata == {"subject_id": "bob", "role_name": "writer"}

    @pytest.mark.asyncio
    async def test_role_changed_records_zanzibar_lists(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
        alice: _FakeUserContext,
    ) -> None:
        await logger.role_changed(
            alice,
            role_id="r-1",
            metadata=RoleChangeMetadata(
                added=["note:abc#admin@user:def"],
                removed=["directory:xyz#writer@user:def"],
            ),
        )
        e = repo.added[0]
        assert e.action == "role_change"
        assert e.role_id == "r-1"
        assert e.metadata == {
            "added": ["note:abc#admin@user:def"],
            "removed": ["directory:xyz#writer@user:def"],
        }

    @pytest.mark.asyncio
    async def test_role_changed_rejects_malformed_added(
        self, logger: DefaultActivityLoggerService, alice: _FakeUserContext,
    ) -> None:
        with pytest.raises(ActivityLoggerError, match="added"):
            await logger.role_changed(
                alice,
                role_id="r-1",
                metadata=RoleChangeMetadata(
                    added=["not-a-zanzibar-string"],
                    removed=[],
                ),
            )

    @pytest.mark.asyncio
    async def test_role_changed_rejects_malformed_removed(
        self, logger: DefaultActivityLoggerService, alice: _FakeUserContext,
    ) -> None:
        with pytest.raises(ActivityLoggerError, match="removed"):
            await logger.role_changed(
                alice,
                role_id="r-1",
                metadata=RoleChangeMetadata(
                    added=[],
                    removed=["also bad"],
                ),
            )

    @pytest.mark.asyncio
    async def test_role_granted_requires_role_id(
        self, logger: DefaultActivityLoggerService, alice: _FakeUserContext,
    ) -> None:
        with pytest.raises(ActivityLoggerError, match="role_id is required"):
            await logger.role_granted(
                alice,
                role_id="",
                metadata=RoleGrantMetadata(subject_id="bob", role_name="writer"),
            )

    @pytest.mark.asyncio
    async def test_role_revoked_requires_role_id(
        self, logger: DefaultActivityLoggerService, alice: _FakeUserContext,
    ) -> None:
        with pytest.raises(ActivityLoggerError, match="role_id is required"):
            await logger.role_revoked(
                alice,
                role_id="",
                metadata=RoleRevokeMetadata(subject_id="bob", role_name="writer"),
            )

    @pytest.mark.asyncio
    async def test_role_changed_requires_role_id(
        self, logger: DefaultActivityLoggerService, alice: _FakeUserContext,
    ) -> None:
        with pytest.raises(ActivityLoggerError, match="role_id is required"):
            await logger.role_changed(
                alice,
                role_id="",
                metadata=RoleChangeMetadata(added=[], removed=[]),
            )


# Misc: actor semantics + error wrapping


class TestActorSemantics:
    """Actor id + ``accessed_as`` are read from the context."""

    @pytest.mark.asyncio
    async def test_system_actor_sets_accessed_as_system(
        self, logger: DefaultActivityLoggerService, repo: _FakeActivityRepo,
    ) -> None:
        sys_ctx = _FakeUserContext(user_id="system-bot", accessed_as="system")
        await logger.note_viewed("n-1", sys_ctx)
        e = repo.added[0]
        assert e.actor_id == "system-bot"
        assert e.accessed_as == "system"

    @pytest.mark.asyncio
    async def test_as_system_returns_decorated_context(self) -> None:
        ctx = _FakeUserContext(user_id="alice", accessed_as="user")
        wrapped = ctx.as_system()
        assert wrapped.accessed_as == "system"
        # original untouched
        assert ctx.accessed_as == "user"
        assert ctx.user_id == "alice"
        assert wrapped.user_id == "alice"


class TestErrorWrapping:
    """A repo failure surfaces as :class:`ActivityLoggerError`."""

    @pytest.mark.asyncio
    async def test_repo_failure_is_wrapped(
        self, alice: _FakeUserContext,
    ) -> None:
        class _BoomRepo(ActivityRepoABC):
            async def get_activities(self, filter): return []
            async def get_most_used(self, filter): return []
            async def add_activity(self, activity):
                raise ValueError("kaboom")
            async def remove_activity_by_id(self, id): pass
            async def edit_activity(self, activity):
                raise ValueError("kaboom")

        logger = DefaultActivityLoggerService(activity_repo=_BoomRepo())
        with pytest.raises(ActivityLoggerError, match="failed to record"):
            await logger.note_viewed("n-1", alice)