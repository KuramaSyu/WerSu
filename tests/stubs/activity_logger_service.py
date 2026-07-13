"""In-memory :class:`ActivityLoggerServiceABC` fake for unit tests.

Records every method call (action, target id, actor id, metadata) so
tests can assert what each service triggered.  Uses the same record
shape as :class:`ActivityEntity` so assertions read naturally:

    fake.calls == [
        ("note_viewed", "note-1", "alice", {}),
        ("note_shared", "note-1", "alice", {"permission": "read"}),
    ]
"""

from __future__ import annotations

from typing import List, Mapping, Optional, Tuple

from src.api.services.activity_logger_service import (
    ActivityLoggerServiceABC,
    RoleChangeMetadata,
    RoleGrantMetadata,
    RoleRevokeMetadata,
)
from src.api.other.user_context import UserContextABC


class _FakeActivityLoggerService(ActivityLoggerServiceABC):
    """In-memory activity logger fake used by service-layer tests."""

    def __init__(self) -> None:
        # Each entry: (action, target_id, actor_id, metadata)
        self.calls: List[
            Tuple[str, str, str, Mapping[str, object]]
        ] = []
        # Role calls: (action, role_id, actor_id, metadata)
        self.role_calls: List[
            Tuple[str, str, str, Mapping[str, object]]
        ] = []

    async def _record(
        self,
        action: str,
        target_id: str,
        actor: UserContextABC,
        metadata: Optional[Mapping[str, object]],
    ) -> None:
        self.calls.append(
            (action, target_id, actor.user_id, dict(metadata or {}))
        )

    async def note_viewed(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("note_viewed", note_id, actor, metadata)

    async def note_created(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("note_created", note_id, actor, metadata)

    async def note_edited(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("note_edited", note_id, actor, metadata)

    async def note_deleted(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("note_deleted", note_id, actor, metadata)

    async def note_published(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("note_published", note_id, actor, metadata)

    async def note_shared(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("note_shared", note_id, actor, metadata)

    async def note_unshared(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("note_unshared", note_id, actor, metadata)

    async def note_restored(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("note_restored", note_id, actor, metadata)

    async def note_archived(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("note_archived", note_id, actor, metadata)

    async def note_version_restored(
        self, note_id: str, actor: UserContextABC, *,
        version: int,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        merged = {"version": version, **(metadata or {})}
        await self._record("note_version_restored", note_id, actor, merged)

    async def note_attachment_added(
        self, note_id: str, actor: UserContextABC, *,
        attachment_id: str,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        merged = {"attachment_id": attachment_id, **(metadata or {})}
        await self._record("note_attachment_added", note_id, actor, merged)

    async def directory_created(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("directory_created", directory_id, actor, metadata)

    async def directory_viewed(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("directory_viewed", directory_id, actor, metadata)

    async def directory_edited(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("directory_edited", directory_id, actor, metadata)

    async def directory_deleted(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record("directory_deleted", directory_id, actor, metadata)

    async def role_granted(
        self, actor: UserContextABC, *,
        role_id: str,
        metadata: RoleGrantMetadata,
    ) -> None:
        self.role_calls.append(
            ("role_grant", role_id, actor.user_id, metadata.__dict__)
        )

    async def role_revoked(
        self, actor: UserContextABC, *,
        role_id: str,
        metadata: RoleRevokeMetadata,
    ) -> None:
        self.role_calls.append(
            ("role_revoke", role_id, actor.user_id, metadata.__dict__)
        )

    async def role_changed(
        self, actor: UserContextABC, *,
        role_id: str,
        metadata: RoleChangeMetadata,
    ) -> None:
        self.role_calls.append(
            ("role_change", role_id, actor.user_id, metadata.__dict__)
        )


__all__ = ["_FakeActivityLoggerService"]