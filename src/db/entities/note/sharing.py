from dataclasses import dataclass
from datetime import datetime


from src.api.undefined import UNDEFINED, UndefinedOr, UndefinedNoneOr


@dataclass
class NoteShareEntity:
    """Represents a share for a note. 
    Use UNDEFINED in case a field is not yet set. Use None, to explicitly set the field to None/Null
    """
    # the id of the share (uuidv7)
    id: UndefinedOr[str] = UNDEFINED

    # a discription which can be None
    description: UndefinedNoneOr[str] = UNDEFINED
    
    # the id of the note which is shared
    note_id: UndefinedOr[str] = UNDEFINED

    # when the share was created
    created_at: UndefinedOr[datetime] = UNDEFINED

    # who created the share (user id)
    created_by: UndefinedOr[datetime] = UNDEFINED

    # when the share is online e.g. accessible from outside
    online_since: UndefinedNoneOr[datetime] = UNDEFINED

    # until when the share is online e.g. accessible from outside. If None, the share does not expire.
    online_until: UndefinedNoneOr[datetime] = UNDEFINED

    # the user under which to access the note linked with note_id. The logged in user,
    # if not granted permission directly, can not access this note. The logged in user
    # will access it under the identity of `access_as` which will have the correct permissions
    access_as: str