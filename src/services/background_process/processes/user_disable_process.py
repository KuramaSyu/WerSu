"""Background process that executes pending ``disable`` user-action rows.

Marks every pending :class:`~src.db.entities.user.user_action.UserActionEntity`
with ``action == "disable"`` whose ``execute_at`` has passed as
executed (``executed_at = now``).
"""

from __future__ import annotations

from src.services.background_process.processes._base import (
    _UserActionByKindProcessImpl,
)


class UserDisableProcessImpl(_UserActionByKindProcessImpl):
    """Execute pending ``disable`` actions when their ``execute_at`` passes."""

    kind = "disable"
    action_name = "DisableUser"