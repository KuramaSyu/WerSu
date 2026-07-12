"""Reusable fakes and stubs for unit tests.

Each stub lives in its own file:

* :mod:`tests.stubs.attachments`     -> attachment repo in-memory fakes
* :mod:`tests.stubs.user_context`   -> tiny :class:`UserContextABC` double
* :mod:`tests.stubs.sharing_repo`   -> in-memory sharing repo
* :mod:`tests.stubs.in_memory_permission_repo` -> in-memory permission repo
* :mod:`tests.stubs.permission_service` -> in-memory permission service
* :mod:`tests.stubs.user_repo`      -> in-memory user repo
* :mod:`tests.stubs.visitor`        -> catch-all :class:`EntityVisitor` for visitor tests
* :mod:`tests.stubs.user_action_repo` -> in-memory user-action repo
* :mod:`tests.stubs.logging`        -> standard-library logger provider
* :mod:`tests.stubs.directory_service` -> in-memory :class:`DirectoryServiceABC`
* :mod:`tests.stubs.activity_logger_service` -> in-memory activity logger fake

The names are also re-exported here so legacy code paths that import
``from tests.stubs import _FakeSharingRepo`` keep working.
"""

from .activity_logger_service import _FakeActivityLoggerService  # noqa: F401
from .attachments import *  # noqa: F401,F403
from .directory_service import _StubDirectoryService  # noqa: F401
from .in_memory_permission_repo import InMemoryPermissionRepo  # noqa: F401
from .logging import silent_logger  # noqa: F401
from .permission_service import _FakePermissionService  # noqa: F401
from .sharing_repo import _FakeSharingRepo  # noqa: F401
from .user_action_repo import _FakeUserActionRepo  # noqa: F401
from .user_context import _UserContext, _UserContextFactory  # noqa: F401
from .user_repo import _FakeUserRepo  # noqa: F401