"""Pytest configuration.

By importing the public sub-modules here, every fixture defined under
:mod:`tests.fixtures` is registered with pytest automatically.  The
public re-export module :mod:`tests.integration_helpers` is also
imported so older tests that destructure ``user_service_env`` and
``sharing_service_env`` continue to work.

To add a new fixture:

* If it does not need any container, add it to ``tests/fixtures.py``.
* If it does, add it under ``tests/fixtures/<service>.py`` and put
  a sibling re-export in ``tests/fixtures/<service>``.
"""

from tests.fixtures import *  # noqa: F401,F403
from tests._fixtures_pkg.spicedb import *  # noqa: F401,F403
from tests._fixtures_pkg.postgres import *  # noqa: F401,F403
from tests._fixtures_pkg.garage import *  # noqa: F401,F403
from tests.integration_helpers import *  # noqa: F401,F403
