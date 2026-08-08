"""One WebAuthn passkey."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, List, Optional

from src.api.other.undefined import UNDEFINED, UndefinedOr
from src.api.other.visitor import AcceptsVisitor, EntityVisitor


@dataclass
class PasskeyEntity(AcceptsVisitor):
    """One WebAuthn passkey.

    ``revoked_at`` is non-null iff the passkey was revoked via
    :meth:`UserPasskeyAuthServiceABC.revoke_passkey`.  Revoked
    rows are hidden from :meth:`list_passkeys` by default.
    """

    id: UndefinedOr[str] = UNDEFINED
    user_id: str = ""
    credential_id: bytes = b""
    public_key: bytes = b""
    sign_count: int = 0
    transports: List[str] = field(default_factory=list)
    aaguid: Optional[bytes] = None
    backup_eligible: bool = False
    backup_state: bool = False
    user_verified: bool = False
    friendly_name: Optional[str] = None
    created_at: Optional[_dt.datetime] = None
    last_used_at: Optional[_dt.datetime] = None
    revoked_at: Optional[_dt.datetime] = None

    def visit(self, visitor: EntityVisitor) -> Any:
        """Dispatch this passkey to ``visitor.visit_passkey``."""
        return visitor.visit_passkey(self)


__all__ = ["PasskeyEntity"]
