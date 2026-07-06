"""Abstract service for recording ``activity`` events.

The :class:`ActivityLoggerServiceABC` exposes one method per
:class:`~src.db.entities.activity.ActivityKind`.  This per-kind surface
is deliberate: the alternative (a single ``log(kind, target, ...)``
method) would push kind-specific parameters back into the call site
and force callers to keep the kind / kwargs mapping in their head.

Every method:

* takes an :class:`~src.api.user_context.UserContextABC` so the
  actor's id and ``accessed_as`` (user / system) are recorded;
* constructs the right :class:`~src.api.activity.ActivityRepoABC`
  entity with the per-kind target shape;
* wraps any underlying exception in :class:`ActivityLoggerError`.

Implementations:
* :class:`src.services.activity_logger_service.PostgresActivityLoggerService`
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from src.api.activity import ActivityRepoABC
from src.api.user_context import UserContextABC
from src.api.types import LoggingProvider


# Errors


class ActivityLoggerError(RuntimeError):
    """Wraps every failure raised while recording an activity event.

    The underlying exception is preserved in :attr:`__cause__` so
    callers that want to inspect the original error can still do so
    via ``except ActivityLoggerError as e: ... e.__cause__``.
    """


# Role-event metadata


@dataclass(frozen=True)
class RoleGrantMetadata:
    """Payload recorded for a ``role_grant`` event.

    Attributes:
        subject_id: id of the user the role was applied to.
        role_name: name of the role at the time of the action.
            Snapshot -- the role may have been renamed later.
    """

    subject_id: str
    role_name: str


@dataclass(frozen=True)
class RoleRevokeMetadata:
    """Payload recorded for a ``role_revoke`` event.

    Attributes:
        subject_id: id of the user the role was removed from.
        role_name: name of the role at the time of the action.
            Snapshot.
    """

    subject_id: str
    role_name: str


@dataclass(frozen=True)
class RoleChangeMetadata:
    """Payload recorded for a ``role_change`` event.

    Captures the precise SpiceDB relation diff.  Each string is a
    zanzibar relation of the form
    ``"<object_type>:<object_id>#<relation>@<subject_type>:<subject_id>"``
    (e.g. ``"note:abc#admin@user:def"``).  Strings that don't match
    the format are rejected by the service so the metadata column
    stays queryable.

    Attributes:
        added: relations added by this change.
        removed: relations removed by this change.
    """

    added: List[str]
    removed: List[str]


# Match: <object_type>:<object_id>#<relation>@<subject_type>:<subject_id>
# Non-greedy on object_id and subject_id so a ``:`` inside an id
# (if one ever shows up) won't break parsing later.
_ZANZIBAR_RELATION_RE = re.compile(
    r"^[a-z_]+:[^#]+#[a-z_]+@[a-z_]+:.+$"
)


def _validate_zanzibar_relations(relations: List[str], *, kind: str) -> None:
    """Reject any string that doesn't look like a zanzibar relation.

    Args:
        relations: list to validate.
        kind: ``"added"`` or ``"removed"`` -- used in the error
            message so callers can tell which list failed.

    Raises:
        ActivityLoggerError: if any string fails the format check.
    """
    for s in relations:
        if not _ZANZIBAR_RELATION_RE.fullmatch(s):
            raise ActivityLoggerError(
                f"role_change {kind} entry {s!r} is not a valid "
                f"zanzibar relation (expected "
                f"'<object_type>:<object_id>#<relation>@<subject_type>:<subject_id>')"
            )


# ABC


class ActivityLoggerServiceABC(ABC):
    """Records ``activity`` events with a typed per-kind surface.

    Methods are split by the *kind* of event they record.  Notes get
    one method per action; directories likewise; roles get three
    (grant / revoke / change).  Each method:

    * ``actor`` is always the first / required positional argument
      because every log row needs an actor;
    * the kind-specific payload (``version``, ``attachment_id``,
      ``role_id``, ``metadata``) is passed as kwargs;
    * any repo failure is wrapped in :class:`ActivityLoggerError`.

    Implementations:
    * :class:`src.services.activity_logger_service.PostgresActivityLoggerService`
    """

    # ----- note-target methods -----

    @abstractmethod
    async def note_viewed(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` viewed ``note_id``."""

    @abstractmethod
    async def note_created(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` created ``note_id``."""

    @abstractmethod
    async def note_edited(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` edited ``note_id``."""

    @abstractmethod
    async def note_deleted(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` deleted ``note_id``."""

    @abstractmethod
    async def note_published(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` published ``note_id``."""

    @abstractmethod
    async def note_shared(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` shared ``note_id``."""

    @abstractmethod
    async def note_unshared(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` tore down a share on ``note_id``."""

    @abstractmethod
    async def note_restored(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` restored ``note_id``."""

    @abstractmethod
    async def note_archived(
        self, note_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` archived ``note_id``."""

    @abstractmethod
    async def note_version_restored(
        self, note_id: str, actor: UserContextABC, *,
        version: int,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` restored ``note_id`` to ``version``."""

    @abstractmethod
    async def note_attachment_added(
        self, note_id: str, actor: UserContextABC, *,
        attachment_id: str,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` added ``attachment_id`` to ``note_id``."""

    # ----- directory-target methods -----

    @abstractmethod
    async def directory_created(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` created ``directory_id``."""

    @abstractmethod
    async def directory_viewed(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` viewed ``directory_id``."""

    @abstractmethod
    async def directory_edited(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` edited ``directory_id``."""

    @abstractmethod
    async def directory_deleted(
        self, directory_id: str, actor: UserContextABC, *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Record that ``actor`` deleted ``directory_id``."""

    # ----- role-target methods -----

    @abstractmethod
    async def role_granted(
        self, actor: UserContextABC, *,
        role_id: str,
        metadata: RoleGrantMetadata,
    ) -> None:
        """Record that ``actor`` granted a role to ``metadata.subject_id``.

        Args:
            actor: caller identity (grantor).
            role_id: id of the role being granted.  Roles are global;
                there is no note / directory scope.
            metadata: snapshot payload -- subject id and the role
                name at the time of the grant.
        """

    @abstractmethod
    async def role_revoked(
        self, actor: UserContextABC, *,
        role_id: str,
        metadata: RoleRevokeMetadata,
    ) -> None:
        """Record that ``actor`` revoked a role from ``metadata.subject_id``."""

    @abstractmethod
    async def role_changed(
        self, actor: UserContextABC, *,
        role_id: str,
        metadata: RoleChangeMetadata,
    ) -> None:
        """Record that ``actor`` changed the relations of a role.

        The :class:`RoleChangeMetadata` carries the precise SpiceDB
        relation diff -- which tuples were added and which were
        removed.  Both lists are validated against the zanzibar
        string format before insert.
        """


__all__ = [
    "ActivityLoggerError",
    "ActivityLoggerServiceABC",
    "RoleChangeMetadata",
    "RoleGrantMetadata",
    "RoleRevokeMetadata",
]