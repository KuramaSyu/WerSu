"""Postgres-backed rule repo.

Re-exports the implementation so callers can write
``from src.db.repos.rule import PostgresRuleRepo``.
"""

from src.db.repos.rule.postgres import PostgresRuleRepo

__all__ = ["PostgresRuleRepo"]
