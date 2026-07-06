"""Postgres-backed implementation of :class:`ActivityLoggerServiceABC`.

Each method builds the right :class:`~src.db.entities.activity.ActivityEntity`
and calls :meth:`ActivityRepoABC.add_activity`.  Any underlying failure
is wrapped in :class:`~src.api.activity_logger_service.ActivityLoggerError`
so callers have one error type to catch.

The :class:`LoggingProvider` is threaded through so the service can
trace failed writes; services that want to drop logging entirely can
inject a no-op provider.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Mapping, Optional

from src.api.activity import ActivityRepoABC
from src.api.activity_logger_service import (
    ActivityLoggerError,
    ActivityLoggerServiceABC,
    RoleChangeMetadata,
    RoleGrantMetadata,
    RoleRevokeMetadata,
    _validate_zanzibar_relations,
)
from src.api.types import LoggingProvider
from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.entities.activity import ActivityEntity
from src.utils import logging_provider as default_logging_provider


class DefaultActivityLoggerService(ActivityLoggerServiceABC):
    """Postgres-backed activity logger.

    Args:
        activity_repo: repo used to insert rows.  No read paths.
        logging_provider: optional logger factory; falls back to
            :func:`src.utils.logging_provider`.
    """

    def __init__(
        self,
        activity_repo: ActivityRepoABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._activity_repo = activity_repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)


    @staticmethod
    def _actor_id(actor: UserContextABC) -> str:
        """Return the actor's user id; raise on missing context."""
        if actor is None:
            raise ActivityLoggerError("actor is required")
        try:
            return actor.user_id
        except NotImplementedError:
            # UnimplementedUserContext surfaces NotImplementedError for
            # user_id; we still want a usable actor, so fall back to
            # the system identity marker.
            return "system"

    @staticmethod
    def _accessed_as(actor: UserContextABC) -> str:
        """Return whether the actor was the user or the system."""
        return actor.accessed_as

    @staticmethod
    def _meta(metadata: Optional[Mapping[str, object]]) -> Mapping[str, object]:
        """Coerce ``None`` to an empty dict so the repo sees a payload."""
        return metadata if metadata is not None else {}

    async def _record(
        self,
        *,
        action: str,
        actor: UserContextABC,
        target: dict,
        metadata: Mapping[str, object],
    ) -> None:
        """Build the entity, insert it, wrap failures.

        ``target`` carries whichever of ``note_id`` / ``directory_id``
        / ``role_id`` applies for ``action``.  The other two target
        fields are explicitly cleared to ``None`` so the row stores
        ``NULL`` rather than leaving the column unset.
        """
        # The kind prefix tells us which target column applies; the
        # other two must be NULL on the row.
        for column in ("note_id", "directory_id", "role_id"):
            target.setdefault(column, None)

        entity = ActivityEntity(
            id=UNDEFINED,
            actor_id=self._actor_id(actor),
            accessed_as=self._accessed_as(actor),
            action=action,  # type: ignore[arg-type]
            **target,
            metadata=dict(metadata),
        )
        try:
            await self._activity_repo.add_activity(entity)
        except ActivityLoggerError:
            raise
        except Exception as exc:
            self.log.warning(
                "activity insert failed",
                extra={"action": action, "actor": self._actor_id(actor)},
                exc_info=exc,
            )
            raise ActivityLoggerError(
                f"failed to record activity {action!r}"
            ) from exc


    async def note_viewed(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="note_viewed",
            actor=actor,
            target={"note_id": note_id},
            metadata=self._meta(metadata),
        )

    async def note_created(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="note_created",
            actor=actor,
            target={"note_id": note_id},
            metadata=self._meta(metadata),
        )

    async def note_edited(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="note_edited",
            actor=actor,
            target={"note_id": note_id},
            metadata=self._meta(metadata),
        )

    async def note_deleted(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="note_deleted",
            actor=actor,
            target={"note_id": note_id},
            metadata=self._meta(metadata),
        )

    async def note_published(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="note_published",
            actor=actor,
            target={"note_id": note_id},
            metadata=self._meta(metadata),
        )

    async def note_shared(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="note_shared",
            actor=actor,
            target={"note_id": note_id},
            metadata=self._meta(metadata),
        )

    async def note_unshared(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="note_unshared",
            actor=actor,
            target={"note_id": note_id},
            metadata=self._meta(metadata),
        )

    async def note_restored(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="note_restored",
            actor=actor,
            target={"note_id": note_id},
            metadata=self._meta(metadata),
        )

    async def note_archived(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="note_archived",
            actor=actor,
            target={"note_id": note_id},
            metadata=self._meta(metadata),
        )

    async def note_version_restored(
        self, note_id: str, actor: UserContextABC, *,
        version: int,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        merged = {"version": version, **(self._meta(metadata))}
        await self._record(
            action="note_version_restored",
            actor=actor,
            target={"note_id": note_id},
            metadata=merged,
        )

    async def note_attachment_added(
        self, note_id: str, actor: UserContextABC, *,
        attachment_id: str,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        merged = {"attachment_id": attachment_id, **(self._meta(metadata))}
        await self._record(
            action="note_attachment_added",
            actor=actor,
            target={"note_id": note_id},
            metadata=merged,
        )


    async def directory_created(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="directory_created",
            actor=actor,
            target={"directory_id": directory_id},
            metadata=self._meta(metadata),
        )

    async def directory_viewed(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="directory_viewed",
            actor=actor,
            target={"directory_id": directory_id},
            metadata=self._meta(metadata),
        )

    async def directory_edited(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="directory_edited",
            actor=actor,
            target={"directory_id": directory_id},
            metadata=self._meta(metadata),
        )

    async def directory_deleted(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        await self._record(
            action="directory_deleted",
            actor=actor,
            target={"directory_id": directory_id},
            metadata=self._meta(metadata),
        )


    async def role_granted(
        self, actor: UserContextABC, *,
        role_id: str,
        metadata: RoleGrantMetadata,
    ) -> None:
        if not role_id:
            raise ActivityLoggerError("role_id is required for role_granted")
        await self._record(
            action="role_grant",
            actor=actor,
            target={"role_id": role_id},
            metadata=dict(asdict(metadata)),
        )

    async def role_revoked(
        self, actor: UserContextABC, *,
        role_id: str,
        metadata: RoleRevokeMetadata,
    ) -> None:
        if not role_id:
            raise ActivityLoggerError("role_id is required for role_revoked")
        await self._record(
            action="role_revoke",
            actor=actor,
            target={"role_id": role_id},
            metadata=dict(asdict(metadata)),
        )

    async def role_changed(
        self, actor: UserContextABC, *,
        role_id: str,
        metadata: RoleChangeMetadata,
    ) -> None:
        if not role_id:
            raise ActivityLoggerError("role_id is required for role_changed")
        _validate_zanzibar_relations(metadata.added, kind="added")
        _validate_zanzibar_relations(metadata.removed, kind="removed")
        await self._record(
            action="role_change",
            actor=actor,
            target={"role_id": role_id},
            metadata=dict(asdict(metadata)),
        )


__all__ = ["DefaultActivityLoggerService"]