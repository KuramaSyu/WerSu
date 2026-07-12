"""In-memory :class:`PermissionRepoABC` implementation for unit tests.

Drop-in replacement for the previous ``NotePermissionRepoInMemory``
in ``src/db.repos.permissions.permission``: stores explicit
relationships as written and resolves implied permissions from a
small, deterministic implication map (e.g. ``owner -> view``).

Lives under ``tests.stubs`` because it is a test double; production
code uses :class:`src.db.repos.permissions.permission.NotePermissionRepoSpicedb`.
"""

from __future__ import annotations

from copy import deepcopy
from typing import List

from src.api.permission_repo import PermissionRepoABC, ResolvedChildren
from src.api.relationship import (
    AttachmentRelationEnum,
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.undefined import UNDEFINED, is_undefined
from src.api.user_context import UserContextABC


class InMemoryPermissionRepo(PermissionRepoABC):
    """In-memory permission repo fake for unit tests.

    Stores explicit relationships exactly as written and resolves
    implied permissions from a static implication map.  Intentionally
    does not simulate full Zanzibar graph traversal semantics; only
    the cases the project's tests exercise are mirrored.
    """

    _relation_implied_permissions = {
        "note": {
            "admin": {"admin", "delete", "write", "view", "edit_permissions"},
            "writer": {"writer", "write", "view"},
            "reader": {"reader", "view"},
            "owner": {"owner", "admin", "delete", "write", "view", "edit_permissions"},
        },
        "directory": {
            "admin": {"admin", "delete", "write", "view", "edit_permissions"},
            "writer": {"writer", "write", "view"},
            "reader": {"reader", "view"},
        },
        "attachment": {
            "admin": {"admin", "delete", "write", "view", "edit_permissions"},
            "writer": {"writer", "write", "view"},
            "reader": {"reader", "view"},
            "owner": {"owner", "admin", "delete", "write", "view", "edit_permissions"},
        },
    }

    def __init__(self) -> None:
        self._store: List[Relationship] = []

    async def insert(self, relationships: List[Relationship]) -> List[Relationship]:
        self._store.extend(deepcopy(relationships))
        return relationships

    async def delete(self, relationship: Relationship) -> Relationship:
        def matches(stored: Relationship) -> bool:
            obj_match = (
                stored.resource.object_type == relationship.resource.object_type
                and (
                    relationship.resource.object_id is UNDEFINED
                    or stored.resource.object_id == relationship.resource.object_id
                )
            )
            rel_match = stored.relation == relationship.relation
            subj_match = (
                stored.subject.object_type == relationship.subject.object_type
                and (
                    relationship.subject.object_id is UNDEFINED
                    or stored.subject.object_id == relationship.subject.object_id
                )
            )
            return obj_match and rel_match and subj_match

        self._store = [r for r in self._store if not matches(r)]
        return relationship

    async def lookup(self, relationship: Relationship) -> List[str]:
        """Dispatch to the matching lookup and return just the ids.

        Exactly one of ``relationship.resource.object_id`` and
        ``relationship.subject.object_id`` must be
        :obj:`~src.api.undefined.UNDEFINED`; the other side must be
        a concrete id.  Anything else raises :exc:`ValueError`.
        """
        resource_id_undefined = is_undefined(relationship.resource.object_id)
        subject_id_undefined = is_undefined(relationship.subject.object_id)
        if resource_id_undefined == subject_id_undefined:
            raise ValueError(
                "lookup() requires exactly one of "
                "relationship.resource.object_id / "
                "relationship.subject.object_id to be UNDEFINED"
            )

        if resource_id_undefined:
            refs = await self._lookup_resources(relationship)
            return [str(ref.object_id) for ref in refs]
        refs = await self._lookup_subjects(relationship)
        return [str(ref.object_id) for ref in refs]

    async def _lookup_resources(self, relationship: Relationship) -> List[ObjectRef]:
        """Return every resource for ``relationship`` matching the user subject.

        Filters stored relations by resource type, subject type+id,
        and relation (when supplied); then expands the relation to
        effective permissions via
        :attr:`_relation_implied_permissions` so callers asking for
        ``view`` match stored ``owner`` / ``writer`` / ``reader``
        entries.
        """
        resource_type = relationship.resource.object_type
        subject_type = relationship.subject.object_type
        subject_id = relationship.subject.object_id
        requested_relation = relationship.relation

        matched: dict[str, ObjectRef] = {}
        implied_map = self._relation_implied_permissions.get(resource_type, {})

        for stored in self._store:
            if stored.resource.object_type != resource_type:
                continue
            if stored.subject.object_type != subject_type:
                continue
            if subject_id is not UNDEFINED and stored.subject.object_id != subject_id:
                continue
            if requested_relation is not UNDEFINED:
                implied = implied_map.get(stored.relation, {stored.relation})
                if str(requested_relation) not in implied:
                    continue
            resource_id = stored.resource.object_id
            if not isinstance(resource_id, str):
                continue
            matched[resource_id] = ObjectRef(
                object_type=resource_type,
                object_id=resource_id,
            )
        return list(matched.values())

    async def _lookup_subjects(self, relationship: Relationship) -> List[ObjectRef]:
        """Return every subject id holding ``relation`` on ``relationship.resource``.

        Filters stored relations by resource type+id, subject type,
        and relation (when supplied); then expands the relation to
        effective permissions via
        :attr:`_relation_implied_permissions` so callers asking for
        ``view`` match stored ``owner`` / ``writer`` / ``reader``
        entries.
        """
        resource_type = relationship.resource.object_type
        resource_id = relationship.resource.object_id
        subject_type = relationship.subject.object_type
        requested_relation = relationship.relation

        matched: dict[str, ObjectRef] = {}
        implied_map = self._relation_implied_permissions.get(resource_type, {})

        for stored in self._store:
            if stored.resource.object_type != resource_type:
                continue
            if resource_id is not UNDEFINED and stored.resource.object_id != resource_id:
                continue
            if stored.subject.object_type != subject_type:
                continue
            if requested_relation is not UNDEFINED:
                implied = implied_map.get(stored.relation, {stored.relation})
                if str(requested_relation) not in implied:
                    continue
            subject_id = stored.subject.object_id
            if not isinstance(subject_id, str):
                continue
            matched[subject_id] = ObjectRef(
                object_type=subject_type,
                object_id=subject_id,
            )
        return list(matched.values())

    async def lookup_relationships(self, relationship: Relationship) -> List[Relationship]:
        relationships: List[Relationship] = []
        for stored in self._store:
            obj_match = (
                stored.resource.object_type == relationship.resource.object_type
                and (
                    relationship.resource.object_id is UNDEFINED
                    or stored.resource.object_id == relationship.resource.object_id
                )
            )
            rel_match = stored.relation == relationship.relation
            subj_match = (
                stored.subject.object_type == relationship.subject.object_type
                and (
                    relationship.subject.object_id is UNDEFINED
                    or stored.subject.object_id == relationship.subject.object_id
                )
            )
            if obj_match and rel_match and subj_match:
                relationships.append(deepcopy(stored))
        return relationships

    async def list_relationships(self, resource: ObjectRef) -> List[Relationship]:
        relationships: List[Relationship] = []
        for stored in self._store:
            if (
                stored.resource.object_type == resource.object_type
                and (
                    resource.object_id is UNDEFINED
                    or stored.resource.object_id == resource.object_id
                )
            ):
                relationships.append(deepcopy(stored))

        return relationships

    async def has_permission(
        self,
        user: UserContextABC,
        permission: str,
        resource: ObjectRef,
    ) -> bool:
        permissions = await self.get_permissions(user=user, resource=resource)
        return permission in permissions

    async def check(self, relationship: Relationship) -> bool:
        # Resolve to whether the user has the requested permission on
        # the resource, expanded via the implication map (so
        # ``owner -> view`` is recognised even though only the
        # ``owner`` relation is stored).  The production SpiceDB does
        # this server-side; mirroring the expansion here keeps tests
        # that rely on transitive permissions realistic.
        if relationship.subject.object_type != "user":
            return bool(await self.lookup_relationships(relationship))
        if relationship.resource.object_id in (UNDEFINED, None):
            return bool(await self.lookup_relationships(relationship))

        # Build a synthetic UserContext for ``get_permissions``.
        class _StubUser:
            def __init__(self, user_id: str) -> None:
                self.user_id = user_id

            @property
            def type(self):  # pragma: no cover - unused here
                return UNDEFINED

            @property
            def accessed_as(self):  # pragma: no cover - unused here
                return "user"

            async def is_temporary_user(self) -> bool:  # pragma: no cover - unused
                return False

        stub = _StubUser(str(relationship.subject.object_id))
        effective = await self.get_permissions(
            stub,
            ObjectRef(
                object_type=relationship.resource.object_type,
                object_id=str(relationship.resource.object_id),
            ),
        )
        return str(relationship.relation) in effective

    async def get_permissions(self, user: UserContextABC, resource: ObjectRef) -> List[str]:
        assert resource.object_id != UNDEFINED, "object_id must be provided for permission checks"

        # Collect direct relationships for this user-resource pair,
        # then expand to effective permissions via the static implication map.
        direct_relations: List[str] = []
        for stored in self._store:
            if (
                stored.resource.object_type == resource.object_type
                and stored.resource.object_id == resource.object_id
                and stored.subject.object_type == "user"
                and stored.subject.object_id == user.user_id
            ):
                direct_relations.append(stored.relation)

        implied_map = self._relation_implied_permissions.get(resource.object_type, {})
        permissions = set[str]()
        for relation in direct_relations:
            # Keep unknown relations as-is so tests can still work with custom schemas.
            permissions.update(implied_map.get(relation, {relation}))

        return sorted(permissions)

    async def resolve_children(
        self,
        directory_id: str,
        *,
        max_depth: int = 10,
        exclusive: bool = True,
    ) -> ResolvedChildren:
        """Walk the subtree rooted at ``directory_id`` over ``self._store``.

        Mirrors the directory-walk portion of the production
        ``NotePermissionRepoSpicedb.resolve_children``, then expands
        to notes and attachments.  ``exclusive`` mirrors the
        production semantics: a note or attachment is included only
        when **every** one of its parent relations sits inside the
        resolved subtree.
        """
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")

        root = str(directory_id)

        # 1. Walk ``directory#parent@directory`` to collect every
        #    reachable directory id (root included).
        sub_directory_ids: set[str] = {root}
        queue: list[tuple[str, int]] = [(root, 0)]
        while queue:
            current_id, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            for rel in self._store:
                if (
                    str(rel.resource.object_type) == ObjectTypeEnum.DIRECTORY.value
                    and str(rel.relation) == DirectoryRelationEnum.PARENT
                    and str(rel.subject.object_type) == ObjectTypeEnum.DIRECTORY.value
                    and str(rel.subject.object_id) == current_id
                    and rel.resource.object_id not in (UNDEFINED, None)
                ):
                    child_id = str(rel.resource.object_id)
                    if child_id not in sub_directory_ids:
                        sub_directory_ids.add(child_id)
                        queue.append((child_id, depth + 1))

        # 2. Collect every note that has at least one
        #    ``note#parent_directory@directory`` relation pointing
        #    into the subtree.
        note_ids: set[str] = set()
        for rel in self._store:
            if (
                str(rel.resource.object_type) == ObjectTypeEnum.NOTE.value
                and str(rel.relation) == NoteRelationEnum.PARENT_DIRECTORY
                and str(rel.subject.object_type) == ObjectTypeEnum.DIRECTORY.value
                and rel.subject.object_id in sub_directory_ids
                and rel.resource.object_id not in (UNDEFINED, None)
            ):
                note_ids.add(str(rel.resource.object_id))

        # 3. Same for attachments.
        attachment_ids: set[str] = set()
        for rel in self._store:
            if (
                str(rel.resource.object_type) == ObjectTypeEnum.ATTACHMENT.value
                and str(rel.relation) == AttachmentRelationEnum.PARENT_NOTE
                and str(rel.subject.object_type) == ObjectTypeEnum.NOTE.value
                and rel.subject.object_id in note_ids
                and rel.resource.object_id not in (UNDEFINED, None)
            ):
                attachment_ids.add(str(rel.resource.object_id))

        if exclusive:
            # Drop notes whose only parents aren't all inside the
            # subtree, and attachments similarly.
            exclusive_notes: set[str] = set()
            for note_id in note_ids:
                parents: set[str] = set()
                for rel in self._store:
                    if (
                        str(rel.resource.object_type) == ObjectTypeEnum.NOTE.value
                        and str(rel.resource.object_id) == note_id
                        and str(rel.relation) == NoteRelationEnum.PARENT_DIRECTORY
                        and str(rel.subject.object_type) == ObjectTypeEnum.DIRECTORY.value
                        and rel.subject.object_id not in (UNDEFINED, None)
                    ):
                        parents.add(str(rel.subject.object_id))
                if parents and parents.issubset(sub_directory_ids):
                    exclusive_notes.add(note_id)
            note_ids = exclusive_notes

            exclusive_attachments: set[str] = set()
            for attachment_key in attachment_ids:
                parents: set[str] = set()
                for rel in self._store:
                    if (
                        str(rel.resource.object_type) == ObjectTypeEnum.ATTACHMENT.value
                        and str(rel.resource.object_id) == attachment_key
                        and str(rel.relation) == AttachmentRelationEnum.PARENT_NOTE
                        and str(rel.subject.object_type) == ObjectTypeEnum.NOTE.value
                        and rel.subject.object_id not in (UNDEFINED, None)
                    ):
                        parents.add(str(rel.subject.object_id))
                if parents and parents.issubset(note_ids):
                    exclusive_attachments.add(attachment_key)
            attachment_ids = exclusive_attachments

        return ResolvedChildren(
            sub_directory_ids=sorted(sub_directory_ids),
            note_ids=sorted(note_ids),
            attachment_ids=sorted(attachment_ids),
        )


__all__ = ["InMemoryPermissionRepo"]