"""Tests for the ``grpc_type_helper`` tri-state wrappers."""

from __future__ import annotations

import pytest
from google.protobuf.empty_pb2 import Empty

from src.api.other.undefined import UNDEFINED, is_undefined
from src.grpc_mod.proto.auth_pb2 import UpdateUserAuthRequest
from src.grpc_mod.proto.note_pb2 import (
    AlterDirectoryRequest,
    AlterNoteRequest,
    IdsOrUndefined,
)
from src.utils.grpc_type_helper import (
    grpc_unwrap_oneof,
    grpc_unwrap_optional,
    grpc_unwrap_tri_state,
)


# ---------- grpc_unwrap_tri_state ----------


class TestGrpcUnwrapTriState:
    """Tri-state mapping for the ``oneof { _set: T, _clear: Empty }`` shape."""

    def _req(self, **kwargs) -> UpdateUserAuthRequest:
        return UpdateUserAuthRequest(user_id="u1", **kwargs)

    def test_omitted_oneof_returns_undefined(self) -> None:
        """No arm selected means 'skip the column'."""
        result = grpc_unwrap_tri_state(
            self._req(),
            "username_change",
            set_arm="username_set",
            clear_arm="username_clear",
        )
        assert is_undefined(result)

    def test_set_arm_returns_value(self) -> None:
        """The ``_set`` arm exposes the inner string verbatim."""
        result = grpc_unwrap_tri_state(
            self._req(username_set="alice"),
            "username_change",
            set_arm="username_set",
            clear_arm="username_clear",
        )
        assert result == "alice"
        assert not is_undefined(result)

    def test_set_arm_preserves_empty_string(self) -> None:
        """An empty ``_set`` is a deliberate value, not a skip."""
        result = grpc_unwrap_tri_state(
            self._req(username_set=""),
            "username_change",
            set_arm="username_set",
            clear_arm="username_clear",
        )
        assert result == ""
        assert not is_undefined(result)

    def test_clear_arm_returns_none(self) -> None:
        """The ``_clear`` arm maps to ``None`` so the column is NULLed."""
        result = grpc_unwrap_tri_state(
            self._req(username_clear=Empty()),
            "username_change",
            set_arm="username_set",
            clear_arm="username_clear",
        )
        assert result is None
        assert not is_undefined(result)

    def test_clear_arm_is_distinguishable_from_omit(self) -> None:
        """``_clear`` is not the same value as 'no oneof at all'."""
        clear = grpc_unwrap_tri_state(
            self._req(username_clear=Empty()),
            "username_change",
            set_arm="username_set",
            clear_arm="username_clear",
        )
        omitted = grpc_unwrap_tri_state(
            self._req(),
            "username_change",
            set_arm="username_set",
            clear_arm="username_clear",
        )
        assert clear is None
        assert is_undefined(omitted)

    def test_clear_arm_uses_custom_clear_value(self) -> None:
        """``clear_value`` lets callers override the default ``None``.

        Used by the avatar REST contract where ``_clear`` writes
        an empty string rather than NULL.
        """
        result = grpc_unwrap_tri_state(
            self._req(avatar_url_clear=Empty()),
            "avatar_url_change",
            set_arm="avatar_url_set",
            clear_arm="avatar_url_clear",
            clear_value="",
        )
        assert result == ""
        assert not is_undefined(result)

    @pytest.mark.parametrize(
        "set_arm,clear_arm,oneof_name",
        [
            ("email_set", "email_clear", "email_change"),
            ("avatar_url_set", "avatar_url_clear", "avatar_url_change"),
            ("username_set", "username_clear", "username_change"),
        ],
    )
    def test_reusable_across_oneofs(
        self, set_arm: str, clear_arm: str, oneof_name: str,
    ) -> None:
        """The helper is parameter-driven and works for any ``_set``/``_clear`` oneof."""
        req = self._req(**{set_arm: "x"})
        assert grpc_unwrap_tri_state(
            req, oneof_name, set_arm=set_arm, clear_arm=clear_arm,
        ) == "x"


# ---------- grpc_unwrap_optional ----------


class TestGrpcUnwrapOptional:
    """Mapping for proto3 ``optional`` scalar fields."""

    def test_unset_optional_returns_undefined(self) -> None:
        """``optional string`` not set on the wire surfaces as :data:`UNDEFINED`."""
        # AlterNoteRequest.author_id is `optional string`.
        req = AlterNoteRequest(id="n1", user_id="u1")
        assert is_undefined(grpc_unwrap_optional(req, "author_id"))

    def test_set_optional_returns_value(self) -> None:
        """A set ``optional string`` returns its inner value."""
        req = AlterNoteRequest(id="n1", user_id="u1", author_id="u2")
        assert grpc_unwrap_optional(req, "author_id") == "u2"

    def test_set_optional_preserves_empty_string(self) -> None:
        """An explicit ``optional string = ""`` is distinct from unset."""
        req = AlterNoteRequest(id="n1", user_id="u1", author_id="")
        result = grpc_unwrap_optional(req, "author_id")
        assert result == ""
        assert not is_undefined(result)


# ---------- grpc_unwrap_oneof ----------


class TestGrpcUnwrapOneof:
    """Mapping for list-shaped ``oneof`` arms (``IdsOrUndefined``)."""

    def test_omitted_returns_undefined(self) -> None:
        """No arm selected means 'skip the list column'."""
        req = AlterNoteRequest(id="n1", user_id="u1")
        assert is_undefined(grpc_unwrap_oneof(req, "directory_ids_change"))

    def test_set_arm_returns_inner_list(self) -> None:
        """Selecting the arm exposes the ``ids`` list verbatim."""
        req = AlterNoteRequest(
            id="n1", user_id="u1",
            directory_ids=IdsOrUndefined(ids=["a", "b"]),
        )
        assert grpc_unwrap_oneof(req, "directory_ids_change") == ["a", "b"]

    def test_set_arm_with_empty_list(self) -> None:
        """An arm with an empty inner list still returns ``[]``, not :data:`UNDEFINED`."""
        req = AlterNoteRequest(
            id="n1", user_id="u1",
            directory_ids=IdsOrUndefined(ids=[]),
        )
        result = grpc_unwrap_oneof(req, "directory_ids_change")
        assert result == []
        assert not is_undefined(result)

    def test_works_for_directory_request(self) -> None:
        """Same shape on ``AlterDirectoryRequest`` for the directory service."""
        req = AlterDirectoryRequest(
            id="d1", user_id="u1",
            shelf_ids=IdsOrUndefined(ids=["s1"]),
        )
        assert grpc_unwrap_oneof(req, "shelf_ids_change") == ["s1"]