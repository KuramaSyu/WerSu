"""Password row for one user."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Optional

from src.api.other.visitor import AcceptsVisitor, EntityVisitor


@dataclass
class PasswordEntity(AcceptsVisitor):
    """One password row per user; ``user_id`` is the PK."""

    user_id: str = ""
    password_hash: str = ""
    created_at: Optional[_dt.datetime] = None
    updated_at: Optional[_dt.datetime] = None

    def visit(self, visitor: EntityVisitor) -> Any:
        """Dispatch this password row to ``visitor.visit_password``."""
        return visitor.visit_password(self)


__all__ = ["PasswordEntity"]
