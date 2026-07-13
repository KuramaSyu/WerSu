"""Unit tests for :meth:`PermissionRepoABC.lookup`.

Pins the dispatch contract: exactly one of
``relationship.resource.object_id`` /
``relationship.subject.object_id`` must be :obj:`~src.api.undefined.UNDEFINED`,
and the result must be the corresponding ids.  Other shapes raise
:exc:`ValueError`.

Backed by :class:`tests.stubs.in_memory_permission_repo.InMemoryPermissionRepo`;
the SpiceDB path is exercised end-to-end by the integration suite.
"""

import pytest

from src.api import ObjectRef, Relationship, SubjectRef
from src.api.undefined import UNDEFINED
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.user_context import _UserContext as UserContext


async def test_lookup_resources_returns_resource_ids_when_resource_id_undefined() -> None:
    """``resource.object_id == UNDEFINED`` returns every resource id."""
    repo = InMemoryPermissionRepo()
    await repo.insert([
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation="reader",
            subject=SubjectRef("user", "alice"),
        ),
        Relationship(
            resource=ObjectRef("note", "note-2"),
            relation="writer",
            subject=SubjectRef("user", "alice"),
        ),
    ])

    ids = await repo.lookup(
        Relationship(
            resource=ObjectRef("note", UNDEFINED),
            relation="view",
            subject=SubjectRef("user", "alice"),
        )
    )

    assert sorted(ids) == ["note-1", "note-2"]


async def test_lookup_subjects_returns_subject_ids_when_subject_id_undefined() -> None:
    """``subject.object_id == UNDEFINED`` returns every subject id."""
    repo = InMemoryPermissionRepo()
    await repo.insert([
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation="reader",
            subject=SubjectRef("user", "alice"),
        ),
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation="reader",
            subject=SubjectRef("user", "bob"),
        ),
    ])

    ids = await repo.lookup(
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation="view",
            subject=SubjectRef("user", UNDEFINED),
        )
    )

    assert sorted(ids) == ["alice", "bob"]


async def test_lookup_expands_relation_via_implication_map() -> None:
    """Asking for ``view`` returns ids stored under implied relations (owner/writer/reader)."""
    repo = InMemoryPermissionRepo()
    await repo.insert([
        Relationship(
            resource=ObjectRef("note", "owner-note"),
            relation="owner",
            subject=SubjectRef("user", "alice"),
        ),
        Relationship(
            resource=ObjectRef("note", "reader-note"),
            relation="reader",
            subject=SubjectRef("user", "alice"),
        ),
        Relationship(
            resource=ObjectRef("note", "writer-note"),
            relation="writer",
            subject=SubjectRef("user", "alice"),
        ),
    ])

    ids = await repo.lookup(
        Relationship(
            resource=ObjectRef("note", UNDEFINED),
            relation="view",
            subject=SubjectRef("user", "alice"),
        )
    )

    assert sorted(ids) == ["owner-note", "reader-note", "writer-note"]


async def test_lookup_filters_by_subject_when_resource_id_undefined() -> None:
    """Resource lookup restricts to the named subject."""
    repo = InMemoryPermissionRepo()
    await repo.insert([
        Relationship(
            resource=ObjectRef("note", "alice-note"),
            relation="owner",
            subject=SubjectRef("user", "alice"),
        ),
        Relationship(
            resource=ObjectRef("note", "bob-note"),
            relation="owner",
            subject=SubjectRef("user", "bob"),
        ),
    ])

    ids = await repo.lookup(
        Relationship(
            resource=ObjectRef("note", UNDEFINED),
            relation="view",
            subject=SubjectRef("user", "alice"),
        )
    )

    assert ids == ["alice-note"]


async def test_lookup_filters_by_resource_when_subject_id_undefined() -> None:
    """Subject lookup restricts to the named resource."""
    repo = InMemoryPermissionRepo()
    await repo.insert([
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation="reader",
            subject=SubjectRef("user", "alice"),
        ),
        Relationship(
            resource=ObjectRef("note", "note-2"),
            relation="reader",
            subject=SubjectRef("user", "alice"),
        ),
    ])

    ids = await repo.lookup(
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation="view",
            subject=SubjectRef("user", UNDEFINED),
        )
    )

    assert ids == ["alice"]


async def test_lookup_raises_when_both_ids_undefined() -> None:
    """Both ``resource.object_id`` and ``subject.object_id`` UNDEFINED is a usage error."""
    repo = InMemoryPermissionRepo()

    with pytest.raises(ValueError, match="exactly one"):
        await repo.lookup(
            Relationship(
                resource=ObjectRef("note", UNDEFINED),
                relation="view",
                subject=SubjectRef("user", UNDEFINED),
            )
        )


async def test_lookup_raises_when_neither_id_undefined() -> None:
    """Both ids concrete is a usage error -- the caller should use ``check``."""
    repo = InMemoryPermissionRepo()

    with pytest.raises(ValueError, match="exactly one"):
        await repo.lookup(
            Relationship(
                resource=ObjectRef("note", "note-1"),
                relation="view",
                subject=SubjectRef("user", "alice"),
            )
        )


async def test_lookup_resources_returns_empty_list_when_no_matches() -> None:
    """Empty store -> empty result, not an error."""
    repo = InMemoryPermissionRepo()

    assert await repo.lookup(
        Relationship(
            resource=ObjectRef("note", UNDEFINED),
            relation="view",
            subject=SubjectRef("user", UserContext("alice").user_id),
        )
    ) == []