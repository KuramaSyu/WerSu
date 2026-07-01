from dataclasses import dataclass
from datetime import datetime
from typing import Literal


from src.api.undefined import UNDEFINED, UndefinedOr, UndefinedNoneOr
from src.api.visitor import AcceptsVisitor, EntityVisitor


@dataclass
class NoteShareEntity(AcceptsVisitor):
    """Represents a share for a note.
    Use UNDEFINED in case a field is not yet set. Use None, to explicitly set the field to None/Null
    """
    # the id of the share (uuidv7)
    id: UndefinedOr[str] = UNDEFINED

    # a description which can be None
    description: UndefinedNoneOr[str] = UNDEFINED
    
    # the id of the note which is shared
    note_id: UndefinedOr[str] = UNDEFINED

    # when the share was created
    created_at: UndefinedOr[datetime] = UNDEFINED

    # who created the share (user id)
    created_by: UndefinedOr[str] = UNDEFINED

    # when the share is online e.g. accessible from outside
    online_since: UndefinedNoneOr[datetime] = UNDEFINED

    # until when the share is online e.g. accessible from outside. If None, the share does not expire.
    online_until: UndefinedNoneOr[datetime] = UNDEFINED

    # the user under which to access the note linked with note_id. The logged in user,
    # if not granted permission directly, can not access this note. The logged in user
    # will access it under the identity of `access_as` which will have the correct permissions
    access_as: UndefinedOr[str] = UNDEFINED

    # the permission on this share
    permission: UndefinedOr[Literal["read", "write"]] = UNDEFINED

    def visit(self, visitor: EntityVisitor):
        """Dispatch this share to ``visitor.visit_note_share``."""
        return visitor.visit_note_share(self)


@dataclass
class FilterShareNote:
    """Filter criteria for searching note shares.

    `UNDEFINED` means the field is ignored. `None` on nullable datetime
    fields searches for rows where that value is NULL.
    """
    # find shares for exactly this note
    note_id: UndefinedOr[str] = UNDEFINED

    # find shares created by exactly this user
    created_by: UndefinedOr[str] = UNDEFINED

    # find shares whose online_since is after or equal this timestamp
    online_since: UndefinedNoneOr[datetime] = UNDEFINED

    # find shares whose online_until is before or equal this timestamp
    online_until: UndefinedNoneOr[datetime] = UNDEFINED

    # find shares accessed as exactly this user
    access_as: UndefinedOr[str] = UNDEFINED

    # find shares with exactly this permission
    permission: UndefinedOr[str] = UNDEFINED