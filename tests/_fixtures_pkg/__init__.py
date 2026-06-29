"""Shared pytest fixtures, grouped by concern.

Importing this package makes every fixture defined in the submodules
visible to pytest (see ``tests/conftest.py``).

The submodules are split by infrastructure dependency so individual
tests can opt in to only what they need:

* :mod:`tests.fixtures.spicedb_schema`  -> SpiceDB container + client helpers.
* :mod:`tests.fixtures.spicedb`         -> SpiceDB-backed ``permission_repo``,
                                           including the one used by all three
                                           permission-flavored integration suites.
* :mod:`tests.fixtures.postgres`        -> Postgres + SpiceDB combined env.
* :mod:`tests.fixtures.garage`          -> Garage S3 container + client.
* :mod:`tests.fixtures.fakes`           -> In-memory test doubles for unit tests.

The older ``tests/integration_helpers.py`` module re-exports the names
that integration tests already import, so the new layout is backwards
compatible.
"""
