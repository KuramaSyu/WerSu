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
import typing
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, is_undefined
from src.api.other.user_context import UserContextABC
from src.api.other.visitor import EntityVisitor
from src.api.repos.activity_repo import ActivityRepoABC

if typing.TYPE_CHECKING:
    from src.db.entities.directory.directory import DirectoryEntity
    from src.db.entities.note.metadata import NoteEntity


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


# Note / directory target metadata


class EventMetadataVisitor(EntityVisitor):
    """Visitor that turns a note / directory into an activity metadata dict.

    Concrete :class:`~src.api.other.visitor.EntityVisitor` subclass
    that builds the per-event ``metadata`` payload.  Only
    :meth:`visit_note` and :meth:`visit_directory` are meaningful --
    every other :meth:`visit_*` raises :exc:`NotImplementedError`
    because the activity log only cares about note / directory
    targets.  The :meth:`visit_*` methods return the dict that will
    ride on ``metadata_json``; missing / unset entity fields are
    dropped from the dict so the row stays queryable.

    Use via :meth:`~src.db.entities.note.metadata.NoteEntity.convert`
    or :meth:`~src.db.entities.directory.directory.DirectoryEntity.convert`:

        note.convert(MetadataVisitor())
        directory.convert(MetadataVisitor())

    When the caller does not have an entity to dispatch from (e.g.
    the ``directory_deleted`` snapshot path), call
    :meth:`visit_note` / :meth:`visit_directory` directly with the
    (possibly `None`) entity.
    """

    def visit_note(self, entity: "NoteEntity") -> Dict[str, object]:
        """Snapshot the note's title under ``note_name``.

        Args:
            entity: note the action targeted.  When `None`, an empty
            dict is returned so callers do not have to special-case
            the missing-snapshot path.

        Returns:
            Dict[str, object]: ``{"note_name": ...}`` when the title
            was known, ``{}`` otherwise.
        """
        if entity is None:
            return {}
        out: Dict[str, object] = {}
        if not is_undefined(entity.title) and entity.title is not None:
            out["note_name"] = entity.title
        return out

    def visit_directory(self, entity: "DirectoryEntity") -> Dict[str, object]:
        """Snapshot the directory's slug and display_name.

        Args:
            entity: directory the action targeted.  When `None`, an
            empty dict is returned.

        Returns:
            Dict[str, object]: ``{"directory_slug": ...,
            "directory_name": ...}`` with only the keys that were
            actually known.
        """
        if entity is None:
            return {}
        out: Dict[str, object] = {}
        if not is_undefined(entity.slug) and entity.slug is not None:
            out["directory_slug"] = entity.slug
        if (
            not is_undefined(entity.display_name)
            and entity.display_name is not None
        ):
            out["directory_name"] = entity.display_name
        return out

    def visit_note_minimal(self, entity: "NoteEntity") -> Dict[str, object]:
        raise NotImplementedError

    def visit_shelf(self, entity: "ShelfEntity") -> Dict[str, object]:
        """Snapshot the shelf's slug and display_name.

        Mirrors :meth:`visit_directory` -- activity log rows for
        shelf-targeted actions land here.  An unknown shelf returns
        ``{}`` so callers can ``.update`` the result without a
        None-guard.

        Args:
            entity: shelf the action targeted.  When ``None``, an
                empty dict is returned.

        Returns:
            Dict[str, object]: ``{"shelf_slug": ...,
            "shelf_name": ...}`` with only the keys that were
            actually known.
        """
        if entity is None:
            return {}
        out: Dict[str, object] = {}
        if not is_undefined(entity.slug) and entity.slug is not None:
            out["shelf_slug"] = entity.slug
        if (
            not is_undefined(entity.display_name)
            and entity.display_name is not None
        ):
            out["shelf_name"] = entity.display_name
        return out

    def visit_user(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_note_share(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_attachment(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_attachment_metadata(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_note_response(self, response: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_activity(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_activity_score(self, score: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_user_auth(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_passkey(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_third_party(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_password(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_role(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError

    def visit_user_role_membership(self, entity: typing.Any) -> Dict[str, object]:
        raise NotImplementedError


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
    * :class:`src.services.activity_logger_service.ActivityLoggerServiceImpl`
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
    "EventMetadataVisitor",
    "RoleChangeMetadata",
    "RoleGrantMetadata",
    "RoleRevokeMetadata",
]