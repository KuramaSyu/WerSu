"""Narrow ``Protocol`` contracts for the action executors.

The rule dispatcher calls only two methods on its injected
collaborators:

* ``add_child_to`` on the directory facade (for
  :class:`~src.api.events.actions.AddToDirectory` actions).
* ``assign_tag_to`` on the tag repo (for
  :class:`~src.api.events.actions.AddTag` actions).

Hence, we use a protocol, to only pick the methods the dispatcher needs
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AddChildToDirectoryCapable(Protocol):
    """Structural type for any object the dispatcher can ask to
    add a note (or directory) as a child of a directory.

    Mirrors :meth:`src.api.repos.directory_repo.DirectoryHelperMixin.add_child_to`.
    The ``parent_type`` literal is always ``"directory"`` for
    dispatcher's :class:`AddToDirectory` action; the protocol
    matches the full mixin signature so the existing facade
    implementation qualifies without an adapter.
    """

    async def add_child_to(
        self,
        parent_type: str,
        parent_id: str,
        child_type: str,
        child_id: str,
    ) -> None:
        """Add ``child_id`` as a child of ``parent_id``."""
        ...


@runtime_checkable
class AssignTagCapable(Protocol):
    """Structural type for any object the dispatcher can ask to
    attach a tag to a note or directory.

    Mirrors :meth:`src.api.repos.tag_repo.TagRepoABC.assign_tag_to`.
    """

    async def assign_tag_to(
        self,
        subject_type: str,
        subject_id: str,
        tag_id: str,
    ) -> None:
        """Attach ``tag_id`` to the entity identified by ``subject_id``."""
        ...


__all__ = ["AddChildToDirectoryCapable", "AssignTagCapable"]
