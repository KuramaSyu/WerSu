"""Cross-layer contract for JWT issuance and verification.

The concrete :class:`src.services.auth.py_jwt_provider.PyJwtProvider`
implementation lives in :mod:`src.services.auth`; this module only
exposes the :class:`JwtProvider` ABC plus the shared
:class:`AttachmentTokenClaims` dataclass and :class:`JwtError`
exception that call sites need regardless of which backend produces
the token.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentTokenClaims:
    """Resolved payload of a verified attachment token.

    Attributes:
        `iss`: token issuer.
        `sub`: id of the user the token authenticates as.
        `att`: id of the attachment the token is scoped to.
        `iat`: issued-at unix timestamp (seconds).
        `exp`: expiry unix timestamp (seconds).
    """

    iss: str
    sub: str
    att: str
    iat: int
    exp: int


class JwtError(Exception):
    """Raised when a token cannot be trusted.

    Covers bad signature, expired or malformed payload, and the case
    where the ``att`` claim does not match the requested attachment.
    """


class JwtProvider(ABC):
    """Abstract factory for JWT issuance and verification.

    Implementations:
    * :class:`src.services.auth.py_jwt_provider.PyJwtProvider`
    """

    @abstractmethod
    def create_attachment_token(
        self,
        user_id: str,
        attachment_id: str,
        *,
        ttl_seconds: int = 15 * 60,
    ) -> str:
        """Issue a token that authenticates `user_id` for `attachment_id`.

        Args:
            user_id: id of the user making the request.
            attachment_id: attachment the token is scoped to (the ``att`` claim).
            ttl_seconds: token lifetime in seconds.

        Returns:
            The encoded JWT string.
        """

    @abstractmethod
    def verify_attachment_token(
        self,
        token: str,
        *,
        expected_attachment_id: str,
    ) -> AttachmentTokenClaims:
        """Verify a token and return its claims.

        Args:
            token: encoded JWT.
            expected_attachment_id: attachment id the request is for;
                must match the token's ``att`` claim.

        Raises:
            JwtError: when the signature is invalid, the token is
                expired or malformed, or the ``att`` claim does not
                match `expected_attachment_id`.

        Returns:
            AttachmentTokenClaims: the verified payload.
        """


__all__ = [
    "AttachmentTokenClaims",
    "JwtError",
    "JwtProvider",
]