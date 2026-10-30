"""Unit tests for the unique-violation translation decorator.

Repo write methods are wrapped with
:func:`src.utils.translate_unique_violation`, which maps
``asyncpg.exceptions.UniqueViolationError`` to
:class:`FriendlyLoginError` so the gRPC layer can return a focused
``ALREADY_EXISTS`` instead of a generic ``INTERNAL``.

Constraint names are migration-defined; the tests pin both the
known names and the field/value extraction.
"""

from __future__ import annotations

import pytest
from asyncpg.exceptions import UniqueViolationError

from src.api.errors import FriendlyLoginError
from src.db.entities.user.user import UserEntity
from src.db.entities.user.user_auth import UserAuthEntity
from src.utils import translate_unique_violation


def _unique_err(constraint: str) -> UniqueViolationError:
    """Build a ``UniqueViolationError`` with a known constraint name.

    asyncpg's exception takes only the message string; the rest
    of the metadata is attached as attributes after construction
    (mirroring how asyncpg populates them at runtime).
    """
    err = UniqueViolationError(
        "duplicate key value violates unique constraint"
    )
    err.constraint_name = constraint
    return err


class _EmailRepo:
    """Repo whose insert always raises on the email constraint."""

    @translate_unique_violation(fields=("email", "username"))
    async def insert(self, user: UserEntity) -> UserEntity:
        raise _unique_err("auth_user_email_uidx")


class _UsernameAuthRepo:
    """Auth repo whose insert always raises on the username constraint."""

    @translate_unique_violation(fields=("email", "username"))
    async def insert(self, user: UserAuthEntity) -> UserAuthEntity:
        raise _unique_err("auth_user_username_uidx")


class _CustomMessageRepo:
    """Repo rendering a caller-supplied message template."""

    @translate_unique_violation(
        "{field} already used: {value}", fields=("email", "username")
    )
    async def insert(self, user: UserEntity) -> UserEntity:
        raise _unique_err("auth_user_email_uidx")


class _CallableMessageRepo:
    """Repo rendering a caller-supplied callable message."""

    @translate_unique_violation(
        lambda field, value: f"dup:{field}:{value}",
        fields=("email", "username"),
    )
    async def insert(self, user: UserEntity) -> UserEntity:
        raise _unique_err("auth_user_email_uidx")


class _UnknownConstraintRepo:
    """Repo whose insert raises on an unrelated constraint."""

    @translate_unique_violation(fields=("email", "username"))
    async def insert(self, user: UserEntity) -> UserEntity:
        raise _unique_err("auth_user_something_else_uidx")


async def test_translates_email_constraint() -> None:
    """Email constraint -> FriendlyLoginError(field='email', value=...)."""
    user = UserEntity(email="alice@example.com", username="alice")

    with pytest.raises(FriendlyLoginError) as exc:
        await _EmailRepo().insert(user)

    assert exc.value.field == "email"
    assert exc.value.value == "alice@example.com"
    assert str(exc.value) == "email is already taken: alice@example.com"


async def test_translates_username_constraint() -> None:
    """Username constraint -> FriendlyLoginError(field='username', value=...)."""
    user = UserAuthEntity(email="alice@example.com", username="alice")

    with pytest.raises(FriendlyLoginError) as exc:
        await _UsernameAuthRepo().insert(user)

    assert exc.value.field == "username"
    assert exc.value.value == "alice"


async def test_custom_message_template() -> None:
    """A str message is formatted with ``{field}`` / ``{value}``."""
    user = UserEntity(email="alice@example.com", username="alice")

    with pytest.raises(FriendlyLoginError) as exc:
        await _CustomMessageRepo().insert(user)

    assert str(exc.value) == "email already used: alice@example.com"


async def test_custom_message_callable() -> None:
    """A callable message receives ``(field, value)``."""
    user = UserEntity(email="alice@example.com", username="alice")

    with pytest.raises(FriendlyLoginError) as exc:
        await _CallableMessageRepo().insert(user)

    assert str(exc.value) == "dup:email:alice@example.com"


async def test_reraises_unknown_constraint() -> None:
    """Unknown constraint name lets the original error bubble up."""
    user = UserEntity(email="alice@example.com", username="alice")

    with pytest.raises(UniqueViolationError) as exc:
        await _UnknownConstraintRepo().insert(user)

    assert exc.value.constraint_name == "auth_user_something_else_uidx"
