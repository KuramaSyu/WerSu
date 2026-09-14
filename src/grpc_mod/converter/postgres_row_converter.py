"""Concrete :class:`EntityVisitor` that projects every entity into a Postgres row dict.

The visitor is the storage-side counterpart to
:class:`~src.grpc_mod.converter.grpc_visitor.ConvertToGrpcVisitor`.
Where the gRPC visitor turns entities into protobuf messages, this
visitor turns them into the plain ``column -> value`` dict that the
:mod:`src.db.table` wrapper hands to asyncpg / SQLite.

Why a visitor?  The ``EntityVisitor`` ABC already routes every
concrete entity to the matching ``visit_*`` handler; reusing the
double-dispatch keeps the conversion logic in one file rather than
scattering per-entity ``to_row_dict`` methods across each dataclass.
Adding a new entity only requires one new method on this class.

JSON columns (``metadata`` / ``condition`` / ``action_context`` /
``extra_fields``) are serialised via :func:`json.dumps` -- matching
the convention already used by
:class:`~src.db.repos.activity.postgres.PostgresActivityRepo`,
:class:`~src.db.repos.rule.postgres.PostgresRuleRepo` and the
``auth.third_party`` ``extra_fields`` column, where asyncpg's
JSONB encoder is bypassed by passing a JSON string instead of a raw
``dict``.  Empty mappings land as the JSON string ``"{}"`` so the
column is always non-null.

Timestamp columns that the entity left :obj:`UNDEFINED` are filled
with the injected ``now()`` callable (default:
:func:`datetime.datetime.now`).  This lets callers keep using
``UndefinedOr[datetime]`` while still benefiting from the database
default of ``NOW()`` -- no caller needs to remember to stamp
``updated_at`` themselves.  The callable is a constructor argument
so tests can pin time deterministically.

Usage:

```python
converter = PostgresRowConverter()
row = converter.visit_note(entity)  # -> dict ready for TableABC.insert
```
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import fields as _dc_fields
from typing import TYPE_CHECKING, Any, Callable, Dict, Mapping, Optional

from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.other.visitor import EntityVisitor
from src.api.services.note_service import NoteResponse
from src.db.entities.activity import ActivityEntity, ActivityScore
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.sharing import NoteShareEntity
from src.db.entities.rule import RuleEntity
from src.db.entities.shelf import ShelfEntity
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.password import PasswordEntity
from src.db.entities.user.role import (
    RoleEntity,
    UserRoleMembershipEntity,
)
from src.db.entities.user.third_party import ThirdPartyEntity
from src.db.entities.user.user import UserEntity
from src.db.entities.user.user_auth import UserAuthEntity

if TYPE_CHECKING:
    # Imported only for type hints: the runtime module imports us
    # back (so it can build Postgres rows in its metadata repo),
    # which would create a circular import at module-load time.
    from src.db.repos.attachments.attachments import Attachment


#: Field names whose JSON values must be serialised via :func:`json.dumps`.
#: Stored as a tuple (not a set) so iteration is deterministic and the
#: membership check stays cheap; lookup is O(1) on a tuple of < 10 entries.
JSON_FIELDS: tuple[str, ...] = (
    "metadata",
    "condition",
    "action_context",
    "extra_fields",
)


#: Field names that should fall back to ``now()`` when the entity left
#: them :obj:`UNDEFINED`.  Anything not in this tuple stays
#: :obj:`UNDEFINED` -- which the table layer drops from the INSERT.
#: ``granted_at`` is intentionally absent: it carries semantic
#: "when the membership was granted" (computed by the service
#: layer) and not "when the row was written".
TIMESTAMP_FIELDS: tuple[str, ...] = (
    "created_at",
    "updated_at",
    "last_used_at",
    "revoked_at",
    "at",
    "execute_at",
    "executed_at",
)


def _default_now() -> _dt.datetime:
    """Default ``now()`` callable used when the repo did not inject one."""
    return _dt.datetime.now()


class PostgresRowConverter(EntityVisitor):
    """Render every entity as a ``column -> value`` dict for Postgres.

    The visitor is stateless except for the injected ``now`` callable;
    instantiate once per repo (or per test) and reuse it.

    Args:
        now: optional callable returning the timestamp used to fill
            out ``TIMESTAMP_FIELDS`` that the entity left
            :obj:`UNDEFINED`.  Defaults to :func:`datetime.datetime.now`;
            tests inject a fixed callable to make time deterministic.
    """

    def __init__(
        self,
        now: Optional[Callable[[], _dt.datetime]] = None,
    ) -> None:
        self._now = now or _default_now

    # ---- helpers -------------------------------------------------------

    @staticmethod
    def _coerce_timestamp(
        value: Any,
        now: Callable[[], _dt.datetime],
    ) -> Any:
        """Return `value` if set, otherwise the current timestamp.

        Two cases trigger the fallback:

        * ``UNDEFINED`` -- the dataclass never populated the
          field (e.g. ``UndefinedOr[datetime]``).
        * ``None`` -- the dataclass carries ``Optional[datetime]``
          and the caller left it unset (e.g.
          :class:`Attachment.created_at`).

        Both produce the current timestamp so the row is always
        concrete when handed to asyncpg.
        """
        if is_undefined(value) or value is None:
            return now()
        return value

    @staticmethod
    def _serialise_json(value: Any) -> str:
        """Serialise `value` as a JSON string suitable for JSONB / TEXT.

        Empty / missing payloads land as ``"{}"`` so the column is
        never ``NULL`` and never crashes asyncpg on the way in.
        Strings (the existing convention for already-serialised
        rows) are returned unchanged so callers can pass either
        shape.
        """
        if value is None or is_undefined(value):
            return "{}"
        if isinstance(value, str):
            return value
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, Mapping):
            try:
                return json.dumps(dict(value), default=str)
            except (TypeError, ValueError):
                return "{}"
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return "{}"

    def _collect_fields(self, entity: Any) -> Dict[str, Any]:
        """Project `entity`'s dataclass fields into a plain dict.

        :obj:`UNDEFINED` values are dropped.  :obj:`None` is
        passed through (caller chose to set the column to NULL);
        :class:`Optional[datetime]` fields that default to
        :obj:`None` are filled by :meth:`_apply_timestamp_fields`
        below.

        Lists / dataclasses are passed through as-is; the table
        layer is responsible for any per-dialect encoding (e.g.
        ``metadata`` JSON serialisation handled below).
        """
        out: Dict[str, Any] = {}
        for field in _dc_fields(entity):
            value = getattr(entity, field.name)
            if is_undefined(value):
                continue
            out[field.name] = value
        return out

    def _apply_json_fields(
        self, entity: Any, row: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Serialise every JSONB column the entity declared.

        Fields that were :obj:`UNDEFINED` are added to the row as
        the JSON string ``"{}"`` -- matches the user-requested
        behaviour ("convert ``{}`` for json with json dumps") and
        means the JSONB column is always populated.

        Only fields the entity actually declares are emitted, so
        an entity that has no ``condition`` column never gets a
        stray ``condition`` key.

        Returns a fresh dict so callers can chain the call without
        mutating the source row.
        """
        try:
            declared = {f.name for f in _dc_fields(entity)}
        except TypeError:
            declared = set()
        out: Dict[str, Any] = {}
        for key in JSON_FIELDS:
            if key not in declared:
                continue
            if key in row:
                out[key] = self._serialise_json(row[key])
            else:
                out[key] = "{}"
        for key, value in row.items():
            if key in JSON_FIELDS:
                continue
            out[key] = value
        return out

    def _apply_timestamp_fields(
        self,
        entity: Any,
        row: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Fill any :data:`TIMESTAMP_FIELDS` the entity left :obj:`UNDEFINED` or :obj:`None`.

        Only fields the entity declared on its dataclass are
        considered -- so a non-dataclass entity (e.g. an
        :class:`Attachment`) still works even though it does not
        carry e.g. ``last_used_at``.
        """
        try:
            entity_fields = {f.name for f in _dc_fields(entity)}
        except TypeError:
            # Not a dataclass -- skip silently.
            return row
        for key in TIMESTAMP_FIELDS:
            if key not in entity_fields:
                continue
            # Use the entity's own value so explicit timestamps
            # survive; otherwise fall back to `now()`.
            row[key] = self._coerce_timestamp(
                getattr(entity, key, UNDEFINED),
                self._now,
            )
        return row

    # ---- entity handlers ------------------------------------------------

    def visit_note(self, entity: NoteEntity) -> Dict[str, Any]:
        """Convert a :class:`NoteEntity` to a Postgres row dict.

        ``note_id`` is renamed to ``id`` to match the table column
        (the dataclass uses ``note_id`` so callers can keep their
        entity-side vocabulary).
        """
        row = self._collect_fields(entity)
        row["id"] = row.pop("note_id", UNDEFINED)
        if is_undefined(row.get("id")):
            # let the DB fill the uuidv7 PK
            row.pop("id", None)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_note_minimal(
        self, entity: NoteEntity
    ) -> Dict[str, Any]:
        """Project a :class:`NoteEntity` to the minimal search-row shape.

        The minimal view excludes the (potentially large)
        ``content`` and the m2m ``directory_ids`` /
        ``tag_ids`` / ``shelf_ids`` -- only the scalars and the
        ``note_id`` PK stay.  Useful for the search index where
        the caller already loaded the heavy columns elsewhere.
        """
        row = self._collect_fields(entity)
        row["id"] = row.pop("note_id", UNDEFINED)
        if is_undefined(row.get("id")):
            row.pop("id", None)
        row.pop("content", None)
        row.pop("directory_ids", None)
        row.pop("tag_ids", None)
        row.pop("shelf_ids", None)
        row.pop("attachment_ids", None)
        row.pop("embeddings", None)
        row.pop("permissions", None)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_note_response(self, response: NoteResponse) -> Dict[str, Any]:
        """Convert a :class:`NoteResponse` to a row dict.

        Pass-through to the inner :class:`NoteEntity` -- the
        ``id_token_map`` only exists on the gRPC response and is
        ignored on the storage side.
        """
        if response.note is None:
            return {}
        return self.visit_note(response.note)

    def visit_directory(self, entity: DirectoryEntity) -> Dict[str, Any]:
        """Convert a :class:`DirectoryEntity` to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_shelf(self, entity: ShelfEntity) -> Dict[str, Any]:
        """Convert a :class:`ShelfEntity` to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_user(self, entity: UserEntity) -> Dict[str, Any]:
        """Convert a :class:`UserEntity` to a Postgres row dict.

        ``discord_id`` and ``discriminator`` are kept -- the
        ``UserPostgresRepo`` strips them explicitly before the
        INSERT because they live on ``auth.third_party`` now.
        """
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_user_auth(self, entity: UserAuthEntity) -> Dict[str, Any]:
        """Convert a :class:`UserAuthEntity` to a Postgres row dict.

        ``third_parties`` is dropped -- it has its own
        ``auth.third_party`` table; the auth repo writes the
        links after the user row lands.
        """
        row = self._collect_fields(entity)
        row.pop("third_parties", None)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_passkey(self, entity: PasskeyEntity) -> Dict[str, Any]:
        """Convert a :class:`PasskeyEntity` to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_third_party(self, entity: ThirdPartyEntity) -> Dict[str, Any]:
        """Convert a :class:`ThirdPartyEntity` to a Postgres row dict.

        ``extra_fields`` is JSON-serialised (matching the
        ``serialised_extras`` property on the entity) so asyncpg
        receives a string for the JSONB column.
        """
        row = self._collect_fields(entity)
        row = self._apply_json_fields(entity, row)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_password(self, entity: PasswordEntity) -> Dict[str, Any]:
        """Convert a :class:`PasswordEntity` to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_role(self, entity: RoleEntity) -> Dict[str, Any]:
        """Convert a :class:`RoleEntity` to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_user_role_membership(
        self, entity: UserRoleMembershipEntity
    ) -> Dict[str, Any]:
        """Convert a :class:`UserRoleMembershipEntity` to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_note_share(self, entity: NoteShareEntity) -> Dict[str, Any]:
        """Convert a :class:`NoteShareEntity` to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_attachment(self, entity: Attachment) -> Dict[str, Any]:
        """Convert an :class:`Attachment` to a Postgres row dict.

        ``content`` (raw bytes) is dropped -- the S3 repo owns
        the payload; the metadata repo only persists the
        descriptive columns.  ``sha256`` is computed by the
        :attr:`Attachment.sha256` property and exposed as
        ``checksum`` on the entity, so the column appears
        under the ``checksum`` key on the row.
        """
        row = self._collect_fields(entity)
        row.pop("content", None)
        # The ``Attachment.sha256`` property lives on the
        # dataclass as ``checksum``; the column in the DB is
        # ``sha256`` -- mirror that translation here so callers
        # can hand the row straight to the table wrapper.
        if "checksum" in row:
            row["sha256"] = row.pop("checksum")
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_attachment_metadata(self, entity: Attachment) -> Dict[str, Any]:
        """Convert an :class:`Attachment` to the metadata-only row dict.

        Identical to :meth:`visit_attachment` today; the split
        exists so future code can return a lighter projection
        without changing the dispatch contract.
        """
        return self.visit_attachment(entity)

    def visit_activity(self, entity: ActivityEntity) -> Dict[str, Any]:
        """Convert an :class:`ActivityEntity` to a Postgres row dict.

        ``metadata`` is JSON-serialised (matching
        :class:`PostgresActivityRepo.add_activity`); ``at`` falls
        back to ``now()`` so callers do not have to stamp the
        timestamp themselves.
        """
        row = self._collect_fields(entity)
        # Strip enrichment fields -- they live on the joined note
        # and are only populated by the activity statistics
        # service for read paths, never persisted on ``activity``.
        row.pop("note_title", None)
        row.pop("note_stripped_content", None)
        row = self._apply_json_fields(entity, row)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_activity_score(
        self, score: ActivityScore
    ) -> Dict[str, Any]:
        """Convert an :class:`ActivityScore` to a Postgres row dict.

        The aggregate ``ActivityScore`` is computed by SQL, not
        persisted -- the conversion still produces a dict so the
        visitor contract stays uniform.
        """
        row = self._collect_fields(score)
        # Convert score to a plain float so JSON serialisation
        # paths downstream can rely on a primitive type.
        if "score" in row and row["score"] is not None:
            row["score"] = float(row["score"])
        return row

    def visit_rule(self, entity: RuleEntity) -> Dict[str, Any]:
        """Convert a :class:`RuleEntity` to a Postgres row dict.

        ``condition`` and ``action_context`` are JSON-serialised
        to match :class:`PostgresRuleRepo._serialise_value`.
        """
        row = self._collect_fields(entity)
        row = self._apply_json_fields(entity, row)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_user_action(self, entity: Any) -> Dict[str, Any]:
        """Convert a :class:`UserActionEntity` to a Postgres row dict.

        ``execute_at`` / ``executed_at`` are filled via the injected
        ``now()`` callable (so a freshly-built scheduled-action row
        always has concrete timestamps).  Mirrors the manual
        ``drop_undefined(asdict(action))`` walk the auth / share
        repos used to do.
        """
        from src.db.entities.user.user_action import UserActionEntity
        assert isinstance(entity, UserActionEntity)
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_note_version_snapshot(self, entity: Any) -> Dict[str, Any]:
        """Convert a :class:`NoteVersionSnapshotEntity` to a Postgres row dict.

        ``created_at`` is filled via the injected ``now()`` callable
        when the entity left it :obj:`UNDEFINED`.
        """
        from src.db.entities.note.versioning import NoteVersionSnapshotEntity
        assert isinstance(entity, NoteVersionSnapshotEntity)
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_note_version_delta(self, entity: Any) -> Dict[str, Any]:
        """Convert a :class:`NoteVersionDeltaEntity` to a Postgres row dict.

        ``created_at`` is filled via the injected ``now()`` callable
        when the entity left it :obj:`UNDEFINED`.
        """
        from src.db.entities.note.versioning import NoteVersionDeltaEntity
        assert isinstance(entity, NoteVersionDeltaEntity)
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    # ---- utility for callers that hold a dataclass instance ------------

    def convert(self, entity: Any) -> Dict[str, Any]:
        """Dispatch `entity` via its :meth:`visit` method.

        The dataclass already routes itself to the matching
        ``visit_*`` handler; this helper centralises the
        ``is_dataclass`` guard so callers that hold an
        arbitrary ``AcceptsVisitor`` do not have to repeat it.
        """
        if hasattr(entity, "visit") and callable(entity.visit):
            return entity.visit(self)
        # Fallback: build a dict directly from the dataclass
        # fields when the object does not implement ``visit``.
        return self._collect_fields(entity)


__all__ = [
    "JSON_FIELDS",
    "TIMESTAMP_FIELDS",
    "PostgresRowConverter",
]
