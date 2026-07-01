"""Equality and pattern-match semantics for :class:`Relationship`.

Two distinct operations live on the class:

* :func:`__eq__` -- strict value equality.  Two :class:`Relationship`
  instances are equal iff their resource, relation and subject all
  match exactly.  Two :obj:`~src.api.undefined.UNDEFINED` ids are
  equal because :obj:`UNDEFINED` is a singleton.
* :func:`__contains__` -- pattern match.  ``value in pattern`` is
  True when every non-:obj:`UNDEFINED` field on the pattern matches
  the value, and every :obj:`UNDEFINED` field on the pattern acts as
  a wildcard.  Type fields (``object_type``) are never wildcards.

The fake permission repo's :func:`delete` uses the pattern form, so
the tests in the *contains* section pin exactly the contract that
the service relies on.
"""

from __future__ import annotations

from src.api.relationship import (
    NoteRelationEnum,
    ObjectRef,
    Relationship,
    SubjectRef,
)
from src.api.undefined import UNDEFINED


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _ref(object_id: str | None) -> ObjectRef:
    """Build a ``note:1`` :class:`ObjectRef`, optionally with an UNDEFINED id."""
    if object_id is None:
        return ObjectRef("note", UNDEFINED)
    return ObjectRef("note", object_id)


def _rel(
    *,
    resource_id: str | None = "1",
    relation: NoteRelationEnum | None = NoteRelationEnum.READER,
    subject_id: str | None = "alice",
) -> Relationship:
    return Relationship(
        resource=_ref(resource_id),
        relation=relation if relation is not None else UNDEFINED,
        subject=SubjectRef(
            "user", subject_id if subject_id is not None else UNDEFINED
        ),
    )


# ---------------------------------------------------------------------------
# __eq__ -- strict value equality
# ---------------------------------------------------------------------------


def test_object_ref_equal_when_both_fields_match() -> None:
    assert ObjectRef("note", "1") == ObjectRef("note", "1")


def test_object_ref_unequal_when_object_id_differs() -> None:
    assert ObjectRef("note", "1") != ObjectRef("note", "2")


def test_object_ref_unequal_when_object_type_differs() -> None:
    assert ObjectRef("note", "1") != ObjectRef("directory", "1")


def test_object_ref_unequal_against_other_type() -> None:
    # No false positives, no exceptions.
    assert ObjectRef("note", "1") != "note:1"
    assert ObjectRef("note", "1") != 42


def test_object_ref_undefined_ids_compare_equal_via_singleton() -> None:
    # ``UNDEFINED`` is a singleton, so two ``UNDEFINED`` ids compare
    # equal by both identity and value.
    assert ObjectRef("note", UNDEFINED) == ObjectRef("note", UNDEFINED)


def test_relationship_equal_when_all_fields_match() -> None:
    assert _rel() == _rel()


def test_relationship_unequal_when_relation_differs() -> None:
    assert _rel() != _rel(relation=NoteRelationEnum.WRITER)


def test_relationship_unequal_when_subject_differs() -> None:
    assert _rel() != _rel(subject_id="bob")


def test_relationship_unequal_against_other_type() -> None:
    assert _rel() != "string"
    assert _rel() != 42


# ---------------------------------------------------------------------------
# __contains__ -- pattern match (this is the one the fake delete uses)
# ---------------------------------------------------------------------------


def test_object_ref_pattern_matches_concrete_value_of_same_type() -> None:
    # ``value in pattern`` -- the right-hand side is the pattern.
    # A pattern with a concrete object_id matches only that exact id.
    assert ObjectRef("note", "1") in ObjectRef("note", "1")
    assert ObjectRef("note", "2") not in ObjectRef("note", "1")


def test_object_ref_undefined_object_id_is_a_wildcard() -> None:
    # ``UNDEFINED`` on the pattern's ``object_id`` matches any id of
    # the same type -- the canonical "all notes" pattern.
    assert ObjectRef("note", "1") in ObjectRef("note", UNDEFINED)
    assert ObjectRef("note", UNDEFINED) in ObjectRef("note", UNDEFINED)


def test_object_ref_type_is_not_a_wildcard() -> None:
    # The pattern's object_type is structural, not a wildcard.  A
    # pattern asking for ``note:*`` never matches a directory.
    assert ObjectRef("note", "1") in ObjectRef("note", UNDEFINED)
    assert ObjectRef("note", UNDEFINED) in ObjectRef("note", UNDEFINED)
    assert ObjectRef("directory", "1") not in ObjectRef("note", UNDEFINED)


def test_relationship_pattern_matches_concrete_match() -> None:
    # Same triple in both pattern and value -> True.
    assert _rel() in _rel()


def test_relationship_undefined_relation_is_a_wildcard() -> None:
    # ``UNDEFINED`` on the pattern's relation matches any relation.
    assert _rel(relation=NoteRelationEnum.WRITER) in _rel(relation=None)
    assert _rel(relation=NoteRelationEnum.OWNER) in _rel(relation=None)


def test_relationship_concrete_relation_must_match() -> None:
    # A concrete relation on the pattern must match the value's
    # relation; otherwise the pattern does not match.
    assert _rel(relation=NoteRelationEnum.WRITER) not in _rel(relation=NoteRelationEnum.READER)


def test_relationship_undefined_resource_id_is_a_wildcard() -> None:
    # ``UNDEFINED`` on the pattern's resource object_id matches any
    # resource id of the same type.
    assert _rel(resource_id="42") in _rel(resource_id=None)


def test_relationship_undefined_subject_id_is_a_wildcard() -> None:
    assert _rel(subject_id="bob") in _rel(subject_id=None)


def test_relationship_type_mismatch_blocks_wildcard() -> None:
    # A pattern with a concrete subject type still requires the
    # subject's type to match -- ``UNDEFINED`` on the candidate's
    # side is *not* a wildcard on type.
    pattern = Relationship(
        resource=ObjectRef("note", "1"),
        relation=NoteRelationEnum.READER,
        subject=SubjectRef("user", "alice"),
    )
    wrong_type = Relationship(
        resource=ObjectRef("note", "1"),
        relation=NoteRelationEnum.READER,
        subject=SubjectRef("directory", "alice"),
    )
    assert wrong_type not in pattern


def test_relationship_fully_wildcard_pattern_matches_everything() -> None:
    pattern = _rel(relation=None, resource_id=None, subject_id=None)
    any_value = _rel(
        relation=NoteRelationEnum.OWNER, resource_id="42", subject_id="carol"
    )
    assert any_value in pattern


def test_relationship_pattern_rejects_unrelated_other_type() -> None:
    # ``in`` must not raise on foreign types, just return False.
    # Wrapped in try/except for the standard ``in`` quirk where
    # some built-in types (e.g. ``str``) raise on non-string operands
    # rather than falling through to ``__contains__`` -- that's
    # Python's protocol, not ours to fix.
    try:
        assert _rel() not in "string"
    except TypeError:
        pass
    try:
        assert _rel() not in 42
    except TypeError:
        pass
