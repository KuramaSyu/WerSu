"""Background process that executes pending ``enable`` user-action rows.

Marks every pending :class:`~src.db.entities.user.user_action.UserActionEntity`
with ``action == "enable"`` whose ``execute_at`` has passed as
executed (``executed_at = now``).
"""

from __future__ import annotations

from src.services.background_process.processes._base import (
    _UserActionByKindProcessImpl,
)


class UserEnableProcessImpl(_UserActionByKindProcessImpl):
    """Execute pending ``enable`` actions when their ``execute_at`` passes."""

    kind = "enable"
    action_name = "EnableUser"