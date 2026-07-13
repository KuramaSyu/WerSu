"""Unit tests for :class:`ActivityFilterBuilder` and :class:`FilterActivity`.

These tests cover the builder's fluent surface, validation rules,
and the construction of valid :class:`FilterActivity` instances.  No
database connection is required -- the builder is pure logic.
"""

from __future__ import annotations

import pytest

from src.api.other.undefined import UNDEFINED
from src.db.entities.activity import (
    ActivityEntity,
    ActivityFilterBuilder,
    ActivityKind,
    ActivityScore,
    FilterActivity,
)


# Builder validation


class TestBuilderValidation:
    """``build()`` rejects malformed filter shapes."""

    def test_missing_mode_raises(self) -> None:
        """No mode-setter before ``build()`` raises ``ValueError``."""
        with pytest.raises(ValueError, match="use_history"):
            ActivityFilterBuilder().set_note("n-1").build()

    def test_with_algorithm_requires_most_used(self) -> None:
        """``with_algorithm`` outside ``most_used`` mode raises."""
        with pytest.raises(ValueError, match="requires show_most_used"):
            (
                ActivityFilterBuilder()
                .use_history()
                .with_algorithm("count")
                .build()
            )

    def test_unique_per_day_requires_most_used(self) -> None:
        """``unique_per_day`` outside ``most_used`` mode raises."""
        with pytest.raises(ValueError, match="requires show_most_used"):
            (
                ActivityFilterBuilder()
                .use_history()
                .unique_per_day()
                .build()
            )

    def test_days_must_be_positive(self) -> None:
        """``set_days(0)`` and negative values raise."""
        with pytest.raises(ValueError, match="positive"):
            ActivityFilterBuilder().use_history().set_days(0).build()
        with pytest.raises(ValueError, match="positive"):
            ActivityFilterBuilder().use_history().set_days(-1).build()


# Builder happy paths


class TestBuilderHappyPaths:
    """Valid builder chains return a fully-populated :class:`FilterActivity`."""

    def test_minimal_history(self) -> None:
        """The shortest valid chain is ``use_history() -> build()``."""
        f = ActivityFilterBuilder().use_history().build()
        assert f.mode == "history"
        assert f.note_id is UNDEFINED
        assert f.directory_ids is UNDEFINED
        assert f.actor_id is UNDEFINED

    def test_history_with_full_filter(self) -> None:
        """Every optional setter lands on the filter dataclass."""
        f = (
            ActivityFilterBuilder()
            .use_history()
            .set_note("n-1")
            .set_user("u-1")
            .set_accessed_as("system")
            .set_role_id("r-1")
            .set_action("note_edited")
            .set_days(30)
            .set_limit(50)
            .set_offset(10)
            .build()
        )
        assert f.mode == "history"
        assert f.note_id == "n-1"
        assert f.actor_id == "u-1"
        assert f.accessed_as == "system"
        assert f.role_id == "r-1"
        assert f.action == "note_edited"
        assert f.days == 30
        assert f.limit == 50
        assert f.offset == 10
        assert f.algorithm is UNDEFINED

    def test_most_used_with_log_count_unique_per_day(self) -> None:
        """Most-used mode captures all the aggregate knobs."""
        f = (
            ActivityFilterBuilder()
            .show_most_used()
            .with_algorithm("log_count")
            .unique_per_day()
            .set_directory("d-1")
            .set_user("u-1")
            .set_days(30)
            .set_limit(20)
            .build()
        )
        assert f.mode == "most_used"
        assert f.algorithm == "log_count"
        assert f.unique_per_day is True
        assert f.directory_ids == ["d-1"]

    def test_action_set_passes_through(self) -> None:
        """``set_action_set`` stores the values as a tuple."""
        f = (
            ActivityFilterBuilder()
            .use_history()
            .set_action_set("note_viewed", "note_edited")
            .build()
        )
        assert f.action_set == ("note_viewed", "note_edited")
        assert f.action is UNDEFINED

    def test_set_directory_accumulates(self) -> None:
        """``set_directory`` appends; multiple calls compose."""
        f = (
            ActivityFilterBuilder()
            .use_history()
            .set_directory("d-1")
            .set_directory("d-2")
            .set_directory("d-3")
            .build()
        )
        assert f.directory_ids == ["d-1", "d-2", "d-3"]

    def test_set_directory_combines_with_set_note(self) -> None:
        """``set_directory`` and ``set_note`` are no longer mutually exclusive."""
        f = (
            ActivityFilterBuilder()
            .use_history()
            .set_note("n-1")
            .set_directory("d-1")
            .build()
        )
        assert f.note_id == "n-1"
        assert f.directory_ids == ["d-1"]

    def test_set_accessed_as_defaults_to_user(self) -> None:
        """``set_accessed_as()`` without arg defaults to ``"user"``."""
        f = (
            ActivityFilterBuilder()
            .use_history()
            .set_accessed_as()
            .build()
        )
        assert f.accessed_as == "user"

    def test_builder_is_fluent(self) -> None:
        """Every setter returns the builder so chains can keep going."""
        b = ActivityFilterBuilder()
        assert b.use_history() is b
        assert b.set_note("n") is b
        assert b.set_user("u") is b
        assert b.set_accessed_as("user") is b
        assert b.set_role_id("r") is b
        assert b.set_action("note_viewed") is b
        assert b.set_action_set("note_viewed") is b
        assert b.set_days(7) is b
        assert b.set_limit(10) is b
        assert b.set_offset(0) is b
        assert b.set_directory("d-1") is b
        assert b.show_most_used() is b
        assert b.with_algorithm("count") is b
        assert b.unique_per_day() is b


# Entity defaults


class TestActivityEntity:
    """``ActivityEntity`` defaults every field to :obj:`UNDEFINED`."""

    def test_default_entity_has_no_concrete_field(self) -> None:
        """A fresh entity carries no concrete values."""
        e = ActivityEntity()
        assert e.id is UNDEFINED
        assert e.actor_id is UNDEFINED
        assert e.accessed_as is UNDEFINED
        assert e.action is UNDEFINED
        assert e.note_id is UNDEFINED
        assert e.directory_id is UNDEFINED
        assert e.role_id is UNDEFINED
        assert e.at is UNDEFINED
        assert e.metadata is UNDEFINED

    def test_none_vs_undefined_distinction(self) -> None:
        """``None`` clears the column; :obj:`UNDEFINED` leaves it alone."""
        e = ActivityEntity(
            actor_id=None,
            note_id=None,
            directory_id=None,
            role_id=None,
        )
        assert e.actor_id is None
        assert e.note_id is None
        assert e.directory_id is None
        assert e.role_id is None


class TestActivityScore:
    """``ActivityScore`` is the most-used query result row."""

    def test_score_is_required(self) -> None:
        """``note_id`` and ``score`` are positional."""
        s = ActivityScore(note_id="n-1", score=42.0)
        assert s.note_id == "n-1"
        assert s.score == 42.0


# Literal type sanity


def test_activity_kind_is_exhaustive() -> None:
    """The ``ActivityKind`` literal covers every recognisable action."""
    expected = {
        # note-target
        "note_viewed",
        "note_created",
        "note_edited",
        "note_deleted",
        "note_published",
        "note_shared",
        "note_unshared",
        "note_restored",
        "note_archived",
        "note_version_restored",
        "note_attachment_added",
        # directory-target
        "directory_created",
        "directory_viewed",
        "directory_edited",
        "directory_deleted",
        # role-target
        "role_grant",
        "role_revoke",
        "role_change",
    }
    actual = set(ActivityKind.__args__)
    assert actual == expected