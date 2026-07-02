from abc import ABC, abstractmethod
from dataclasses import dataclass
import time
from typing import Callable

import jwt


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
    """Abstract factory for JWT issuance and verification."""

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


class PyJwtProvider(JwtProvider):
    """Impl of :class:`JwtProvider` using PyJWT.

    Args:
        secret: shared secret used to sign and verify tokens.  Must
            be bytes or a string that can be encoded as UTF-8.
        issuer: value of the `iss` claim.  Defaults to
            ``"WerSu gRPC"``.
        algorithm: JWT algorithm.  Defaults to HS256.
        time: callable returning the current unix time in seconds.
            Defaults to :func:`time.time`.  Override in tests to drive
            ``iat`` / ``exp`` deterministically.
    """

    def __init__(
        self,
        secret: str | bytes,
        *,
        issuer: str = "WerSu gRPC",
        algorithm: str = "HS256",
        time: Callable[[], float] = time.time,
    ) -> None:
        self._secret = secret.encode() if isinstance(secret, str) else secret
        self._issuer = issuer
        self._algorithm = algorithm
        self._time = time

    def create_attachment_token(
        self,
        user_id: str,
        attachment_id: str,
        *,
        ttl_seconds: int = 15 * 60,
    ) -> str:
        now = int(self._time())
        payload = {
            "iss": self._issuer,
            "sub": user_id,
            "att": attachment_id,
            "iat": now,
            "exp": now + ttl_seconds,
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def verify_attachment_token(
        self,
        token: str,
        *,
        expected_attachment_id: str,
    ) -> AttachmentTokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={"require": ["iss", "sub", "att", "exp"]},
            )
        except jwt.PyJWTError as exc:
            raise JwtError(str(exc)) from exc

        if payload.get("att") != expected_attachment_id:
            raise JwtError(
                f"token att={payload.get('att')!r} does not match "
                f"requested attachment={expected_attachment_id!r}"
            )

        return AttachmentTokenClaims(
            iss=payload["iss"],
            sub=payload["sub"],
            att=payload["att"],
            iat=int(payload["iat"]),
            exp=int(payload["exp"]),
        )


__all__ = [
    "AttachmentTokenClaims",
    "JwtError",
    "JwtProvider",
    "PyJwtProvider",
]
    