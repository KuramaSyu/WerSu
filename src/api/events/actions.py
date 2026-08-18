"""Action variants for the rules subsystem.

An :class:`Action` is the "then" part of a rule.  Each variant is
a frozen dataclass with a ``type`` literal field so the JSONB
row in Postgres round-trips through a single
:func:`serialise_action` / :func:`deserialise_action` pair.

Adding a new action is a four-step operation:

1. Add a dataclass subclass with a ``type`` field here.
2. Add the matching ``type`` literal handling in
   :func:`serialise_action` / :func:`deserialise_action`.
3. Add the executor on the service / dispatcher side.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Union


# Discriminator literal naming every supported action type.  Stored
# in the ``rules.action_type`` column so it is indexable; the
# parameters ride on ``action_context`` JSONB.
ActionType = str


# ---- variants --------------------------------------------------------------


@dataclass(frozen=True)
class AddToDirectory:
    """Add the triggering note to ``directory_id``.

    Args:
        directory_id: id of the directory the note will be added
            to.  If the note is already in the directory the action
            is a no-op.
    """

    directory_id: str
    type: ActionType = "add_to_directory"


@dataclass(frozen=True)
class AddTag:
    """Add the given tag to the triggering entity.

    For ``NoteCreated`` / ``NoteUpdated`` events the tag is
    attached to the note; for ``DirectoryCreated`` /
    ``DirectoryUpdated`` events it is attached to the directory.
    If the tag is already attached the action is a no-op.

    Args:
        tag_id: id of the tag to attach.
    """

    tag_id: str
    type: ActionType = "add_tag"


# Union type used by the service / repo layer to type-annotate a
# deserialised action.  Add new variants here.
Action = Union[AddToDirectory, AddTag]


# ---- (de)serialisation ----------------------------------------------------


def serialise_action(action: Action) -> tuple[str, dict[str, Any]]:
    """Turn an action dataclass into ``(action_type, action_context)``.

    The split mirrors the two columns in the ``rules`` table:
    ``action_type`` is the discriminator (TEXT, indexable) and
    ``action_context`` carries the parameters (JSONB).

    Args:
        action: an :class:`Action` instance.

    Returns:
        tuple[str, dict[str, Any]]: ``(action_type, action_context)``.

    Raises:
        TypeError: when ``action`` is not a known variant.
    """
    if not isinstance(action, (AddToDirectory, AddTag)):
        raise TypeError(
            f"unknown action variant: {type(action).__name__}"
        )
    payload = asdict(action)
    return payload["type"], payload


def deserialise_action(
    action_type: str,
    action_context: Mapping[str, Any],
) -> Action:
    """Turn ``(action_type, action_context)`` back into an action dataclass.

    Args:
        action_type: the discriminator column value.
        action_context: the JSONB column value, already
            JSON-decoded by asyncpg.

    Returns:
        Action: the corresponding dataclass instance.

    Raises:
        ValueError: when ``action_type`` is unknown or
            ``action_context`` is missing a required field for a
            known variant.
    """
    if action_type == "add_to_directory":
        directory_id = action_context.get("directory_id")
        if not isinstance(directory_id, str) or not directory_id:
            raise ValueError(
                "add_to_directory requires a non-empty 'directory_id' field"
            )
        return AddToDirectory(directory_id=directory_id)
    if action_type == "add_tag":
        tag_id = action_context.get("tag_id")
        if not isinstance(tag_id, str) or not tag_id:
            raise ValueError(
                "add_tag requires a non-empty 'tag_id' field"
            )
        return AddTag(tag_id=tag_id)
    raise ValueError(f"unknown action type: {action_type!r}")


__all__ = [
    "AddToDirectory",
    "AddTag",
    "Action",
    "serialise_action",
    "deserialise_action",
]
