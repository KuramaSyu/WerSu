"""Specialised background process for executing user-action rows.

A :class:`UserActionProcessABC` is the parent of the two concrete
flavours (:class:`UserDisableProcessABC`,
:class:`UserEnableProcessABC`).  The split exists so the `why`
payload name is statically known per process, which makes logs and
:meth:`BackgroundSchedulerABC.list_events` easier to scan.
"""

from __future__ import annotations

from src.api.services.background_process.background_process import BackgroundProcessABC


class UserActionProcessABC(BackgroundProcessABC):
    """Background process that executes one :class:`UserActionEntity` at a time.

    The `why` payload always uses the process class's
    :attr:`action_name` as ``name``.
    """

    action_name: str  # subclasses set this, e.g. "DisableUser"


class UserDisableProcessABC(UserActionProcessABC):
    """Executes pending ``disable`` actions when their ``execute_at`` passes."""

    action_name = "DisableUser"


class UserEnableProcessABC(UserActionProcessABC):
    """Executes pending ``enable`` actions when their ``execute_at`` passes."""

    action_name = "EnableUser"