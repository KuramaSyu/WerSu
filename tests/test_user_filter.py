"""Tests for :class:`src.api.services.user_service.UserFilter`.

The filter is the canonical way for callers (gRPC adapters, REST
controllers, tests) to describe "find me a user with these
properties".  Every field is :data:`UndefinedOr` so the caller can
leave fields unset; set fields are AND-ed.  These tests pin:

* every field defaults to :obj:`~src.api.undefined.UNDEFINED`
* the dataclass is hashable (so callers can use it as a cache key)
* set fields round-trip through :meth:`__eq__`
* :meth:`UserFilter.is_empty` returns ``True`` iff no field is set
* :meth:`UserFilter.set_fields` returns the names that were set
"""

from __future__ import annotations

from src.api.services.user_service import UserFilter
from src.api.other.undefined import UNDEFINED


def test_user_filter_defaults_all_fields_to_undefined() -> None:
    """A fresh filter has every field set to :obj:`UNDEFINED`."""
    f = UserFilter()

    assert f.user_id is UNDEFINED
    assert f.email is UNDEFINED
    assert f.discord_id is UNDEFINED


def test_user_filter_is_empty_when_no_fields_set() -> None:
    """A fresh filter reports `is_empty()` as ``True``."""
    assert UserFilter().is_empty() is True


def test_user_filter_is_not_empty_when_any_field_is_set() -> None:
    """`is_empty()` flips to ``False`` as soon as any field is set."""
    assert UserFilter(user_id="u-1").is_empty() is False
    assert UserFilter(email="a@b.c").is_empty() is False
    assert UserFilter(discord_id=123).is_empty() is False


def test_user_filter_set_fields_returns_names_of_set_fields() -> None:
    """`set_fields()` lists only the fields the caller populated."""
    assert UserFilter().set_fields() == []
    assert UserFilter(user_id="u-1").set_fields() == ["user_id"]
    assert UserFilter(email="a@b.c", discord_id=1).set_fields() == [
        "email",
        "discord_id",
    ]


def test_user_filter_equality_only_compares_set_fields() -> None:
    """Two filters with the same set fields compare equal.

    The implementation may keep unset fields as the
    :obj:`UNDEFINED` singleton (in which case equality is
    automatic) or as ``None`` (in which case dataclass equality
    would compare ``None`` == ``None``).  Either way, the test
    passes: only the set fields differ between the two filters
    below.
    """
    a = UserFilter(user_id="u-1", email="a@b.c")
    b = UserFilter(user_id="u-1", email="a@b.c")
    assert a == b


def test_user_filter_is_hashable() -> None:
    """Filters can be used as dict keys (e.g. as a cache key)."""
    a = UserFilter(user_id="u-1", email="a@b.c")
    b = UserFilter(user_id="u-1", email="a@b.c")
    assert hash(a) == hash(b)
    assert {a: "value"}[b] == "value"
