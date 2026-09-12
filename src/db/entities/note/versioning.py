from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api.other.visitor import AcceptsVisitor, EntityVisitor


@dataclass
class NoteVersionSnapshotEntity(AcceptsVisitor):
    """Represents a full-content snapshot for a note version."""

    snapshot_id: UndefinedOr[str] = UNDEFINED
    note_id: UndefinedOr[str] = UNDEFINED
    version_index: UndefinedOr[int] = UNDEFINED
    created_at: UndefinedNoneOr[datetime] = UNDEFINED
    author_id: UndefinedNoneOr[str] = UNDEFINED
    title: UndefinedNoneOr[str] = UNDEFINED
    content: UndefinedNoneOr[str] = UNDEFINED

    def visit(self, visitor: EntityVisitor):
        """Dispatch this snapshot to ``visitor.visit_note_version_snapshot``."""
        return visitor.visit_note_version_snapshot(self)


@dataclass
class NoteVersionDeltaEntity(AcceptsVisitor):
    """Represents a delta patch applied after a snapshot."""

    delta_id: UndefinedOr[str] = UNDEFINED
    note_id: UndefinedOr[str] = UNDEFINED
    snapshot_id: UndefinedOr[str] = UNDEFINED
    version_index: UndefinedOr[int] = UNDEFINED
    created_at: UndefinedNoneOr[datetime] = UNDEFINED
    author_id: UndefinedNoneOr[str] = UNDEFINED
    title_patch: UndefinedNoneOr[str] = UNDEFINED
    content_patch: UndefinedNoneOr[str] = UNDEFINED

    def visit(self, visitor: EntityVisitor):
        """Dispatch this delta to ``visitor.visit_note_version_delta``."""
        return visitor.visit_note_version_delta(self)


@dataclass
class NoteVersionEntry:
    """Combined view of a version entry (snapshot or delta)."""

    version_id: str
    note_id: str
    version_index: int
    created_at: datetime
    author_id: str
    is_snapshot: bool
    snapshot_id: Optional[str] = None


@dataclass
class NoteVersionContent:
    """Reconstructed note content for a specific version."""

    note_id: str
    version_index: int
    created_at: datetime
    author_id: str
    title: str
    content: str
