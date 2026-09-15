"""Concrete EntityVisitor that projects every entity into a Postgres row dict."""

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
    # Imported only for type hints: runtime imports us back, which would create a cycle.
    from src.db.repos.attachments.attachments import Attachment


#: Field names whose JSON values must be serialised via json.dumps.
JSON_FIELDS: tuple[str, ...] = (
    "metadata",
    "condition",
    "action_context",
    "extra_fields",
)


#: Field names that fall back to now() when the entity left them UNDEFINED.
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
    """Default now() callable when the repo did not inject one."""
    return _dt.datetime.now()


class PostgresRowConverter(EntityVisitor):
    """Render every entity as a column -> value dict for Postgres.

    Internal infrastructure: instantiated once in the composition
    root and injected into every Postgres repo that writes rows.
    Most callers should not need to override this; tests may inject
    a fixed ``now`` to pin timestamps deterministically.
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
        """Return value if set, otherwise the current timestamp."""
        if is_undefined(value) or value is None:
            return now()
        return value

    @staticmethod
    def _serialise_json(value: Any) -> str:
        """Serialise value as a JSON string suitable for JSONB or TEXT."""
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
        """Project entity dataclass fields into a plain dict; UNDEFINED is dropped."""
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
        """Serialise every JSONB column the entity declared; missing fields become "{}"."""
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
        """Fill TIMESTAMP_FIELDS the entity left UNDEFINED or None with now()."""
        try:
            entity_fields = {f.name for f in _dc_fields(entity)}
        except TypeError:
            # Not a dataclass -- skip silently.
            return row
        for key in TIMESTAMP_FIELDS:
            if key not in entity_fields:
                continue
            # Use the entity's own value; fall back to now() for unset timestamps.
            row[key] = self._coerce_timestamp(
                getattr(entity, key, UNDEFINED),
                self._now,
            )
        return row

    # ---- entity handlers ------------------------------------------------

    def visit_note(self, entity: NoteEntity) -> Dict[str, Any]:
        """Convert a NoteEntity to a Postgres row dict."""
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
        """Project a NoteEntity to the minimal search-row shape."""
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
        """Convert a NoteResponse to a row dict (passes through to the inner note)."""
        if response.note is None:
            return {}
        return self.visit_note(response.note)

    def visit_directory(self, entity: DirectoryEntity) -> Dict[str, Any]:
        """Convert a DirectoryEntity to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_shelf(self, entity: ShelfEntity) -> Dict[str, Any]:
        """Convert a ShelfEntity to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_user(self, entity: UserEntity) -> Dict[str, Any]:
        """Convert a UserEntity to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_user_auth(self, entity: UserAuthEntity) -> Dict[str, Any]:
        """Convert a UserAuthEntity to a Postgres row dict; third_parties is dropped."""
        row = self._collect_fields(entity)
        row.pop("third_parties", None)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_passkey(self, entity: PasskeyEntity) -> Dict[str, Any]:
        """Convert a PasskeyEntity to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_third_party(self, entity: ThirdPartyEntity) -> Dict[str, Any]:
        """Convert a ThirdPartyEntity to a Postgres row dict; extra_fields is JSON-serialised."""
        row = self._collect_fields(entity)
        row = self._apply_json_fields(entity, row)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_password(self, entity: PasswordEntity) -> Dict[str, Any]:
        """Convert a PasswordEntity to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_role(self, entity: RoleEntity) -> Dict[str, Any]:
        """Convert a RoleEntity to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_user_role_membership(
        self, entity: UserRoleMembershipEntity
    ) -> Dict[str, Any]:
        """Convert a UserRoleMembershipEntity to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_note_share(self, entity: NoteShareEntity) -> Dict[str, Any]:
        """Convert a NoteShareEntity to a Postgres row dict."""
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_attachment(self, entity: Attachment) -> Dict[str, Any]:
        """Convert an Attachment to a Postgres row dict; content is dropped, checksum is renamed to sha256."""
        row = self._collect_fields(entity)
        row.pop("content", None)
        # Attachment.sha256 is exposed as checksum on the dataclass; rename back for the column.
        if "checksum" in row:
            row["sha256"] = row.pop("checksum")
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_attachment_metadata(self, entity: Attachment) -> Dict[str, Any]:
        """Convert an Attachment to the metadata-only row dict; same as visit_attachment today."""
        return self.visit_attachment(entity)

    def visit_activity(self, entity: ActivityEntity) -> Dict[str, Any]:
        """Convert an ActivityEntity to a Postgres row dict; metadata is JSON-serialised."""
        row = self._collect_fields(entity)
        # Strip enrichment fields; they live on the joined note and are never persisted on activity.
        row.pop("note_title", None)
        row.pop("note_stripped_content", None)
        row = self._apply_json_fields(entity, row)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_activity_score(
        self, score: ActivityScore
    ) -> Dict[str, Any]:
        """Convert an ActivityScore to a Postgres row dict; score is normalised to float."""
        row = self._collect_fields(score)
        if "score" in row and row["score"] is not None:
            row["score"] = float(row["score"])
        return row

    def visit_rule(self, entity: RuleEntity) -> Dict[str, Any]:
        """Convert a RuleEntity to a Postgres row dict; condition and action_context are JSON-serialised."""
        row = self._collect_fields(entity)
        row = self._apply_json_fields(entity, row)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_user_action(self, entity: Any) -> Dict[str, Any]:
        """Convert a UserActionEntity to a Postgres row dict."""
        from src.db.entities.user.user_action import UserActionEntity
        assert isinstance(entity, UserActionEntity)
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_note_version_snapshot(self, entity: Any) -> Dict[str, Any]:
        """Convert a NoteVersionSnapshotEntity to a Postgres row dict."""
        from src.db.entities.note.versioning import NoteVersionSnapshotEntity
        assert isinstance(entity, NoteVersionSnapshotEntity)
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    def visit_note_version_delta(self, entity: Any) -> Dict[str, Any]:
        """Convert a NoteVersionDeltaEntity to a Postgres row dict."""
        from src.db.entities.note.versioning import NoteVersionDeltaEntity
        assert isinstance(entity, NoteVersionDeltaEntity)
        row = self._collect_fields(entity)
        row = self._apply_timestamp_fields(entity, row)
        return row

    # ---- utility for callers that hold a dataclass instance ------------

    def convert(self, entity: Any) -> Dict[str, Any]:
        """Dispatch entity via its visit method (falls back to _collect_fields)."""
        if hasattr(entity, "visit") and callable(entity.visit):
            return entity.visit(self)
        # Fallback: project the dataclass directly when visit is not implemented.
        return self._collect_fields(entity)


__all__ = [
    "JSON_FIELDS",
    "TIMESTAMP_FIELDS",
    "PostgresRowConverter",
]
