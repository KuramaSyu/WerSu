"""Unit tests for the asyncpg unique-violation translation in the user repos.

The repos map ``asyncpg.exceptions.UniqueViolationError`` to
:class:`FriendlyLoginError` so the gRPC layer can return a
focused ``ALREADY_EXISTS`` instead of a generic ``INTERNAL``.

Constraint names are migration-defined; the tests pin both the
known names and the field/value extraction.
"""

from __future__ import annotations

from asyncpg.exceptions import UniqueViolationError

from src.api.errors import FriendlyLoginError
from src.db.entities.user.user import UserEntity
from src.db.repos.user.user import _translate_unique_violation as user_translate
from src.db.entities.user.user_auth import UserAuthEntity
from src.db.repos.user.user_auth_postgres import (
    _translate_unique_violation as auth_translate,
)


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


def test_user_translate_picks_email_constraint() -> None:
    """Email constraint -> FriendlyLoginError(field='email', value=...)."""
    user = UserEntity(email="alice@example.com", username="alice")
    err = _unique_err("auth_user_email_uidx")

    friendly = user_translate(err, user)

    assert isinstance(friendly, FriendlyLoginError)
    assert friendly.field == "email"
    assert friendly.value == "alice@example.com"


def test_user_translate_picks_username_constraint() -> None:
    """Username constraint -> FriendlyLoginError(field='username', value=...)."""
    user = UserEntity(email="alice@example.com", username="alice")
    err = _unique_err("auth_user_username_uidx")

    friendly = user_translate(err, user)

    assert friendly.field == "username"
    assert friendly.value == "alice"


def test_user_translate_reraises_unknown_constraint() -> None:
    """Unknown constraint name lets the original error bubble up."""
    user = UserEntity(email="alice@example.com", username="alice")
    err = _unique_err("auth_user_something_else_uidx")

    try:
        user_translate(err, user)
    except UniqueViolationError as raised:
        assert raised is err
    else:
        raise AssertionError("expected UniqueViolationError to be re-raised")


def test_auth_translate_picks_email_constraint() -> None:
    """The auth repo helper maps the same constraint name to the same error."""
    user = UserAuthEntity(email="alice@example.com", username="alice")
    err = _unique_err("auth_user_email_uidx")

    friendly = auth_translate(err, user)

    assert friendly.field == "email"
    assert friendly.value == "alice@example.com"


def test_auth_translate_picks_username_constraint() -> None:
    user = UserAuthEntity(email="alice@example.com", username="alice")
    err = _unique_err("auth_user_username_uidx")

    friendly = auth_translate(err, user)

    assert friendly.field == "username"
    assert friendly.value == "alice"
