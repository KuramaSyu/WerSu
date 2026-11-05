"""Deprecated import shim for the renamed role repo.

The implementation is now :class:`PostgresRoleRepo` in
:mod:`src.db.repos.permissions.postgres_role_repo`; it no longer owns
SpiceDB membership logic.
"""

from src.db.repos.permissions.postgres_role_repo import PostgresRoleRepo

SpicedbRoleRepo = PostgresRoleRepo

__all__ = ["PostgresRoleRepo", "SpicedbRoleRepo"]
