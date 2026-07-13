"""Concrete :class:`~src.api.jwt_provider.JwtProvider` backed by PyJWT.

The :class:`JwtProvider` ABC and the :class:`AttachmentTokenClaims`
dataclass stay in :mod:`src.api.jwt_provider` because they are part
of the cross-layer contract; only this concrete implementation lives
here under :mod:`src.services.auth`.
"""

from __future__ import annotations

import time
from typing import Callable

import jwt

from src.api.services.jwt_provider import (
    AttachmentTokenClaims,
    JwtError,
    JwtProvider,
)


class PyJwtProvider(JwtProvider):
    """Impl of :class:`JwtProvider` using PyJWT.

    Args:
        secret: shared secret used to sign and verify tokens.  Must
            be bytes or a string that can be encoded as UTF-8.
        issuer: value of the ``iss`` claim.  Defaults to
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


__all__ = ["PyJwtProvider"]