"""Pytest configuration.

The lightweight fixtures live in :mod:`tests.fixtures`.  The
container-backed fixtures live under :mod:`tests._fixtures_pkg`, split
by infrastructure dependency so a test only pays for what it imports.
Importing the sub-modules here registers every fixture with pytest
automatically.  :mod:`tests.integration_helpers` is also imported so
older tests that destructure ``user_service_env`` and
``sharing_service_env`` continue to work.

To add a new fixture:

* If it does not need any container, add it to ``tests/fixtures.py``.
* If it does, add it under ``tests/_fixtures_pkg/<service>.py`` and add
  a matching ``from tests._fixtures_pkg.<service> import *`` line below.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache

import pytest

from tests.fixtures import *  # noqa: F401,F403
from tests._fixtures_pkg.spicedb import *  # noqa: F401,F403
from tests._fixtures_pkg.postgres import *  # noqa: F401,F403
from tests._fixtures_pkg.garage import *  # noqa: F401,F403
from tests.integration_helpers import *  # noqa: F401,F403


@lru_cache(maxsize=1)
def _docker_available() -> bool:
    """Return True when a usable Docker daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def pytest_collection_modifyitems(config, items):
    """Skip container-backed tests when Docker is unavailable.

    The default ``addopts`` already deselects integration/poc tests, so
    this only kicks in when they are run explicitly.  Without it, running
    them on a machine without Docker fails during testcontainers setup
    instead of skipping with a clear reason.
    """
    integration = [item for item in items if "integration" in item.keywords]
    if not integration or _docker_available():
        return
    skip = pytest.mark.skip(
        reason="Docker is not available; skipping container-backed integration tests"
    )
    for item in integration:
        item.add_marker(skip)
