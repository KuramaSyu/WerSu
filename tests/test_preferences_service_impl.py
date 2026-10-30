"""Unit tests for PreferencesServiceImpl.

The service delegates storage to a PreferencesRepoABC and gates
reads on the scope-appropriate permission chain. Writes currently
pass through un-gated (a TODO in the service flags this), so we
exercise the read-side permission checks here and let the repo
tests cover the storage contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import pytest

from tests.doubles import InMemoryPermissionRepo
from tests.doubles import silent_logger
from tests.doubles import _FakeSpyPreferencesRepo
from tests.doubles import _DummyUserContext as UserContext
from src.api.other.relationship import (
    ObjectRef,
    Relationship,
    RoleRelationEnum,
    ShelfRelationEnum,
    SubjectRef,
)
from src.api.services.preferences_service import PreferenceScope
from src.services.preferences import PreferencesServiceImpl


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _build_service(
    *,
    initial: Optional[Dict[Tuple[PreferenceScope, str], Dict]] = None,
    permissions: Optional[InMemoryPermissionRepo] = None,
) -> tuple[PreferencesServiceImpl, _FakeSpyPreferencesRepo, InMemoryPermissionRepo]:
    perms = permissions or InMemoryPermissionRepo()
    repo = _FakeSpyPreferencesRepo(initial=initial)
    pinned = datetime(2026, 1, 1, tzinfo=timezone.utc)

    service = PreferencesServiceImpl(
        preferences_repo=repo,
        permission_repo=perms,
        logging_provider=silent_logger,
        get_now=lambda: pinned,
    )
    return service, repo, perms


async def _seed(
    perms: InMemoryPermissionRepo, *relationships: Relationship
) -> None:
    await perms.insert(list(relationships))


# ---------------------------------------------------------------------------
# empty target_id
# ---------------------------------------------------------------------------


async def test_get_preferences_rejects_empty_target_id() -> None:
    service, _, _ = _build_service()
    with pytest.raises(ValueError, match="target_id is required"):
        await service.get_preferences("user", "", UserContext("actor"))


@pytest.mark.parametrize(
    "method_name",
    ["update_preferences", "patch_preferences", "delete_preferences"],
)
async def test_write_methods_reject_empty_target_id(
    method_name: str,
) -> None:
    service, _, _ = _build_service()
    method = getattr(service, method_name)
    kwargs = {"ctx": UserContext("actor")}
    if method_name != "delete_preferences":
        kwargs["preferences"] = {"a": 1}
    with pytest.raises(ValueError, match="target_id is required"):
        await method("user", "", **kwargs)


# ---------------------------------------------------------------------------
# payload coercion (update + patch)
# ---------------------------------------------------------------------------


async def test_update_accepts_json_string_payload() -> None:
    service, repo, _ = _build_service()
    result = await service.update_preferences(
        "user", "u1", '{"a": 1, "b": 2}', ctx=UserContext("u1")
    )
    assert result == {"a": 1, "b": 2}
    assert repo.stored("user", "u1") == {"a": 1, "b": 2}


async def test_update_rejects_invalid_json_string() -> None:
    service, _, _ = _build_service()
    with pytest.raises(ValueError, match="valid JSON"):
        await service.update_preferences(
            "user", "u1", "{not json", ctx=UserContext("u1")
        )


async def test_update_rejects_non_object_payload() -> None:
    service, _, _ = _build_service()
    with pytest.raises(ValueError, match="JSON object"):
        await service.update_preferences(
            "user", "u1", "[1, 2, 3]", ctx=UserContext("u1")
        )


async def test_patch_merges_into_existing_blob() -> None:
    service, repo, _ = _build_service(
        initial={("user", "u1"): {"a": 1, "b": 2}}
    )
    result = await service.patch_preferences(
        "user", "u1", {"b": 3, "c": 4}, ctx=UserContext("u1")
    )
    assert result == {"a": 1, "b": 3, "c": 4}
    assert repo.stored("user", "u1") == {"a": 1, "b": 3, "c": 4}


async def test_patch_starts_from_empty_when_no_existing_row() -> None:
    service, repo, _ = _build_service()
    result = await service.patch_preferences(
        "user", "u1", {"k": "v"}, ctx=UserContext("u1")
    )
    assert result == {"k": "v"}
    assert repo.stored("user", "u1") == {"k": "v"}


async def test_patch_uses_injected_timestamp() -> None:
    """The service must thread get_now() into the repo's upsert."""
    service, repo, _ = _build_service(
        initial={("user", "u1"): {"a": 1}}
    )
    await service.patch_preferences(
        "user", "u1", {"b": 2}, ctx=UserContext("u1")
    )
    last_upsert = repo.upsert_calls[-1]
    assert last_upsert[3] == datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_returns_true_when_row_existed() -> None:
    service, repo, _ = _build_service(initial={("user", "u1"): {"a": 1}})
    assert await service.delete_preferences(
        "user", "u1", ctx=UserContext("u1")
    ) is True
    assert repo.stored("user", "u1") == {}


async def test_delete_returns_false_when_no_row() -> None:
    service, _, _ = _build_service()
    assert await service.delete_preferences(
        "user", "u1", ctx=UserContext("u1")
    ) is False


# ---------------------------------------------------------------------------
# permission gating on read
# ---------------------------------------------------------------------------


async def test_get_user_preferences_allowed_for_self() -> None:
    service, repo, _ = _build_service(
        initial={("user", "u1"): {"theme": "dark"}}
    )
    result = await service.get_preferences(
        "user", "u1", ctx=UserContext(user_id="u1")
    )
    assert result == {"theme": "dark"}


async def test_get_user_preferences_allowed_for_system_context() -> None:
    service, repo, _ = _build_service(
        initial={("user", "u1"): {"theme": "dark"}}
    )
    ctx = UserContext(user_id="admin", accessed_as="system")
    result = await service.get_preferences("user", "u1", ctx=ctx)
    assert result == {"theme": "dark"}


async def test_get_user_preferences_rejects_other_user() -> None:
    service, _, _ = _build_service(
        initial={("user", "u1"): {"theme": "dark"}}
    )
    with pytest.raises(PermissionError):
        await service.get_preferences(
            "user", "u1", ctx=UserContext(user_id="attacker")
        )


async def test_get_shelf_preferences_requires_view_perm() -> None:
    perms = InMemoryPermissionRepo()
    service, repo, perms = _build_service(
        initial={("shelf", "shelf-7"): {"col": 1}},
        permissions=perms,
    )
    await _seed(
        perms,
        Relationship(
            resource=ObjectRef("shelf", "shelf-7"),
            relation=ShelfRelationEnum.OWNER,
            subject=SubjectRef("user", "u1"),
        ),
    )
    # Owner -> implied view; reader is not enough for the chain but viewer is.
    result = await service.get_preferences(
        "shelf", "shelf-7", ctx=UserContext(user_id="u1")
    )
    assert result == {"col": 1}

    with pytest.raises(PermissionError):
        await service.get_preferences(
            "shelf", "shelf-7", ctx=UserContext(user_id="stranger")
        )


async def test_get_role_preferences_requires_manage() -> None:
    perms = InMemoryPermissionRepo()
    service, _, perms = _build_service(
        initial={("role", "r1"): {"x": 1}}, permissions=perms
    )
    await _seed(
        perms,
        Relationship(
            resource=ObjectRef("role", "r1"),
            relation=RoleRelationEnum.ADMINISTRATOR,
            subject=SubjectRef("user", "u1"),
        ),
    )
    result = await service.get_preferences(
        "role", "r1", ctx=UserContext(user_id="u1")
    )
    assert result == {"x": 1}

    with pytest.raises(PermissionError):
        await service.get_preferences(
            "role", "r1", ctx=UserContext(user_id="stranger")
        )


async def test_unsupported_scope_raises_value_error() -> None:
    service, _, _ = _build_service()
    with pytest.raises(ValueError, match="unsupported preference scope"):
        await service.get_preferences(
            "bogus",  # type: ignore[arg-type]
            "x",
            ctx=UserContext("actor"),
        )


# ---------------------------------------------------------------------------
# repo interactions
# ---------------------------------------------------------------------------


async def test_read_returns_empty_when_no_row_exists() -> None:
    """get_preferences for an un-set user should yield {} and not raise."""
    service, _, _ = _build_service()
    result = await service.get_preferences(
        "user", "nobody", ctx=UserContext("nobody")
    )
    assert result == {}


async def test_repo_is_called_with_correct_scope_target_pairs() -> None:
    """The service must address the repo keyed on (scope, target_id)."""
    service, repo, _ = _build_service(
        initial={("shelf", "shelf-9"): {"x": 1}}
    )
    perms = service._permission_repo  # type: ignore[attr-defined]
    await _seed(
        perms,  # type: ignore[arg-type]
        Relationship(
            resource=ObjectRef("shelf", "shelf-9"),
            relation=ShelfRelationEnum.OWNER,
            subject=SubjectRef("user", "u1"),
        ),
    )
    await service.get_preferences(
        "shelf", "shelf-9", ctx=UserContext(user_id="u1")
    )

    assert repo.read_calls == [("shelf", "shelf-9")]
