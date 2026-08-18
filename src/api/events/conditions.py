"""Condition variants for the rules subsystem.

A :class:`Condition` is the "if" part of a rule.  Each variant is
a frozen dataclass with a ``type`` literal field so the JSONB row
in Postgres round-trips through a single
:func:`serialise_condition` / :func:`deserialise_condition` pair.

Adding a new condition is a four-step operation:

1. Add a dataclass subclass with a ``type`` field here.
2. Add the matching ``type`` literal to the
   :data:`ConditionType` type alias.
3. Extend :func:`serialise_condition` / :func:`deserialise_condition`
   to handle the new variant.
4. Add the matching evaluator on the listener side -- the
   condition dataclass itself is data only, evaluation lives in
   the rule dispatcher.

The :func:`matches` helper is **not** a method on the dataclass
because the dispatcher already knows which variant it has; an
``isinstance`` switch in the dispatcher is more readable than
virtual dispatch on a data class.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Union


# Discriminator literal that names every supported condition type.
# Keep this in sync with the variants below.
ConditionType = str
"""One of the ``type`` literals declared on the condition dataclasses."""


# ---- variants --------------------------------------------------------------


@dataclass(frozen=True)
class AlwaysTrue:
    """Condition that always evaluates to ``True``.

    Used for rules that should fire for every event of their
    ``event_type`` once other gating (permission to create the
    rule, scope matching) is satisfied.  Carries no parameters.
    """

    type: ConditionType = "always_true"


@dataclass(frozen=True)
class NoteContentContains:
    """True when the current content of ``note_id`` contains ``substring``.

    The dispatcher fetches the current content via
    :meth:`EventContext.note_content` at evaluation time, so this
    dataclass is data-only.

    Args:
        substring: the literal string to look for.  Comparison is
            case-sensitive and substring-based; for more elaborate
            matching add a new variant rather than overloading this one.
    """

    substring: str
    type: ConditionType = "note_content_contains"


@dataclass(frozen=True)
class NoteTitleContains:
    """True when the current title of ``note_id`` contains ``substring``.

    Same matching rules as :class:`NoteContentContains`.
    """

    substring: str
    type: ConditionType = "note_title_contains"


# Union type used by the service / repo layer to type-annotate a
# deserialised condition.  Add new variants here.
Condition = Union[AlwaysTrue, NoteContentContains, NoteTitleContains]


# ---- (de)serialisation ----------------------------------------------------


def serialise_condition(condition: Condition) -> dict[str, Any]:
    """Turn a condition dataclass into the JSONB-shaped dict.

    The ``type`` field is included so the discriminator is part of
    the payload; this matches the shape
    :func:`deserialise_condition` expects.

    Args:
        condition: a :class:`Condition` instance.

    Returns:
        dict[str, Any]: the serialised payload.  Safe to feed to
        ``json.dumps``.

    Raises:
        TypeError: when ``condition`` is not a known variant.
    """
    if not isinstance(condition, (AlwaysTrue, NoteContentContains, NoteTitleContains)):
        raise TypeError(
            f"unknown condition variant: {type(condition).__name__}"
        )
    return asdict(condition)


def deserialise_condition(payload: Mapping[str, Any]) -> Condition:
    """Turn a JSONB-shaped dict back into a condition dataclass.

    Args:
        payload: the stored ``condition`` row (already JSON-decoded
            by asyncpg).  Must contain a ``type`` discriminator.

    Returns:
        Condition: the corresponding dataclass instance.

    Raises:
        ValueError: when the payload is missing ``type``, has an
            unknown ``type``, or is missing a required field for a
            known variant.
    """
    if "type" not in payload:
        raise ValueError("condition payload is missing the 'type' discriminator")

    ctype = payload["type"]
    if ctype == "always_true":
        return AlwaysTrue()
    if ctype == "note_content_contains":
        substring = payload.get("substring")
        if not isinstance(substring, str) or not substring:
            raise ValueError(
                "note_content_requires a non-empty 'substring' field"
            )
        return NoteContentContains(substring=substring)
    if ctype == "note_title_contains":
        substring = payload.get("substring")
        if not isinstance(substring, str) or not substring:
            raise ValueError(
                "note_title_contains requires a non-empty 'substring' field"
            )
        return NoteTitleContains(substring=substring)
    raise ValueError(f"unknown condition type: {ctype!r}")


__all__ = [
    "AlwaysTrue",
    "NoteContentContains",
    "NoteTitleContains",
    "Condition",
    "serialise_condition",
    "deserialise_condition",
]
