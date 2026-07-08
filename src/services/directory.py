"""Concrete :class:`~src.api.directory_service.DirectoryServiceABC` implementation.

This service composes :class:`~src.db.repos.directory.directory.DirectoryRepo`
and :class:`~src.db.repos.note.note.NoteFacade` with the permission repo,
and orchestrates every directory-related concern (permission checks,
README bookkeeping, pagination, default-directory resolution).

It sits above the directories / notes repos but below the gRPC adapter,
so that :class:`~src.grpc_mod.service.GrpcDirectoryService` (and any
future caller) can stay free of permission/repo plumbing.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from src.api import (
    ActivityLoggerServiceABC,
    DirectoryRelationEnum,
    DirectoryServiceABC,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    PermissionRepoABC,
    Relationship,
    SubjectRef,
)
from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.directory.directory import DirectoryRepo
from src.api.note_facade import NoteRepoFacadeABC
from src.domain.permission_chain import (
    HasDirectoryDeletePerm,
    HasDirectoryViewPerm,
    HasDirectoryWritePerm,
)
from src.utils.readme_parser import parse_readme


README_TITLE = "README.md"
"""Title of the auto-managed ``README.md`` note for each directory."""


class DirectoryService(DirectoryServiceABC):
    """Concrete :class:`~src.api.directory_service.DirectoryServiceABC`.

    Every public method gates the call with a permission check from
    :mod:`src.domain.permission_chain` before it touches the underlying
    repos.  The README bookkeeping for
    :meth:`get_directory_notes` is also performed exclusively here.
    """

    def __init__(
        self,
        directory_repo: DirectoryRepo,
        note_repo: NoteRepoFacadeABC,
        permission_repo: PermissionRepoABC,
        activity_logger: ActivityLoggerServiceABC,
    ) -> None:
        self._directory_repo = directory_repo
        self._note_repo = note_repo
        self._permission_repo = permission_repo
        self._activity_logger = activity_logger

    async def get_directory_notes(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
        limit: int,
        offset: int,
    ) -> List[NoteEntity]:
        """Return notes belonging to ``directory_id`` with pagination.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.get_directory_notes`.
        """
        if limit < 0:
            raise ValueError("limit must be >= 0")
        if offset < 0:
            raise ValueError("offset must be >= 0")

        await self._assert_directory_view(directory_id, user_ctx)

        rels = await self._permission_repo.lookup_relationships(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, UNDEFINED),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, directory_id),
            )
        )
        note_ids = [
            str(rel.resource.object_id)
            for rel in rels
            if rel.resource.object_id not in (UNDEFINED, None)
        ]

        notes: List[NoteEntity] = []
        readme: Optional[NoteEntity] = None
        for note_id in note_ids:
            note = await self._note_repo.select_by_id(note_id, user_ctx)
            if note is None:
                continue
            if note.title == README_TITLE:
                if readme is None:
                    readme = note
                continue
            notes.append(note)

        if readme is None:
            readme = await self._create_readme(directory_id, user_ctx)

        ordered_notes = [readme] + notes

        return ordered_notes[offset : offset + limit]

    async def get_directory(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> Optional[DirectoryEntity]:
        """Return a single directory visible to `user_ctx`.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.get_directory`.

        Note:
            Overlays the parsed README note onto the returned
            :class:`~src.db.entities.directory.directory.DirectoryEntity`
            when the directory has a `readme_note_id`.
        """
        await self._assert_directory_view(directory_id, user_ctx)
        directory = await self._directory_repo.fetch_directory(directory_id)
        if directory is None:
            return None
        await self._apply_readme_overrides(directory, user_ctx)
        await self._activity_logger.directory_viewed(directory_id, user_ctx)
        return directory

    async def get_directories(
        self,
        user_ctx: UserContextABC,
        parent_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[DirectoryEntity]:
        """Return all directories visible to `user_ctx`, paginated.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.get_directories`.

        Note:
            Overlays each directory's parsed README note before
            pagination so a linked ``README.md`` drives the displayed
            `image_url` and `description`.
        """
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0")
        if offset is not None and offset < 0:
            raise ValueError("offset must be >= 0")

        directory_ids = await self._directory_repo.list_user_directory_ids(user_ctx)
        directories: List[DirectoryEntity] = []
        for directory_id in directory_ids:
            directory = await self._directory_repo.fetch_directory(directory_id)
            if directory is not None:
                directories.append(directory)

        if parent_id is not None:
            directories = [
                d
                for d in directories
                if d.parent_id not in (UNDEFINED, None)
                and str(d.parent_id) == parent_id
            ]
        for directory in directories:
            await self._apply_readme_overrides(directory, user_ctx)
            # if directory.id not in (UNDEFINED, None):
            #     await self._activity_logger.directory_viewed(
            #         str(directory.id), user_ctx
            #     )
        effective_offset = offset if offset is not None else 0
        if limit is not None:
            return directories[effective_offset : effective_offset + limit]
        return directories[effective_offset:]

    async def create_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: UserContextABC,
    ) -> DirectoryEntity:
        """Create a new directory.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.create_directory`.
        """
        # The new directory itself does not exist yet, so a permission
        # check against it would always fail.  When the entity specifies
        # a parent directory, gate creation on a write check against it
        # so random callers can't nest directories wherever they want.
        if entity.parent_id not in (UNDEFINED, None):
            check = HasDirectoryWritePerm(str(entity.parent_id)).set_permission_repo(
                self._permission_repo
            )
            result = await check.check(user_ctx)
            if not result:
                raise result.error

        admin_relation = Relationship(
            resource=ObjectRef(
                object_type=ObjectTypeEnum.DIRECTORY, object_id=UNDEFINED
            ),
            relation=DirectoryRelationEnum.ADMIN,
            subject=SubjectRef(
                object_type=ObjectTypeEnum.USER, object_id=user_ctx.user_id
            ),
        )

        existing_relations: list[Relationship] = []
        if isinstance(entity.relations, list):
            existing_relations = list(entity.relations)

        created = await self._directory_repo.create_directory(
            DirectoryEntity(
                id=entity.id,
                name=entity.name,
                display_name=entity.display_name,
                description=entity.description,
                image_url=entity.image_url,
                parent_id=entity.parent_id,
                readme_note_id=entity.readme_note_id,
                relations=[admin_relation] + existing_relations,
            )
        )

        # Create the README.md which contains description and image of directory
        # then also insert note#parent_directory@directory relation 
        if created.id not in (UNDEFINED, None):
            existing_readme_id = (
                str(created.readme_note_id)
                if created.readme_note_id not in (UNDEFINED, None, "")
                else None
            )
            if existing_readme_id is None:
                await self._create_readme(str(created.id), user_ctx)
            else:
                await self._bind_readme(
                    str(created.id), existing_readme_id
                )
            refreshed = await self._directory_repo.fetch_directory(
                str(created.id)
            )
            if refreshed is not None:
                created = refreshed
            await self._activity_logger.directory_created(
                str(created.id), user_ctx
            )
        return created

    async def patch_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: UserContextABC,
    ) -> Optional[DirectoryEntity]:
        """Patch an existing directory.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.patch_directory`.
        """
        if entity.id in (UNDEFINED, None, ""):
            raise ValueError("id is required for patch_directory")

        check = HasDirectoryWritePerm(str(entity.id)).set_permission_repo(
            self._permission_repo
        )
        result = await check.check(user_ctx)
        if not result:
            raise result.error

        updated = await self._directory_repo.update_directory(entity)
        if updated is not None:
            await self._activity_logger.directory_edited(
                str(entity.id), user_ctx
            )
        return updated

    async def delete_directory(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> bool:
        """Delete a directory by id.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.delete_directory`.
        """
        check = HasDirectoryDeletePerm(directory_id).set_permission_repo(
            self._permission_repo
        )
        result = await check.check(user_ctx)
        if not result:
            raise result.error

        deleted = await self._directory_repo.delete_directory(
            DirectoryEntity(id=directory_id)
        )
        if deleted:
            await self._activity_logger.directory_deleted(
                directory_id, user_ctx
            )
        return deleted

    async def _assert_directory_view(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        """Raise :exc:`PermissionError` when `user_ctx` cannot view `directory_id`."""
        check = HasDirectoryViewPerm(directory_id).set_permission_repo(
            self._permission_repo
        )
        result = await check.check(user_ctx)
        if not result:
            raise result.error

    async def _create_readme(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> NoteEntity:
        """Create a fresh ``README.md`` for `directory_id` and bind it.

        Inserts an empty README note owned by `user_ctx`, then binds
        it to the directory via :meth:`_bind_readme` so subsequent
        reads resolve it without scanning parent-directory relations.

        Returns:
            :class:`~src.db.entities.note.metadata.NoteEntity`: the
            inserted README with its assigned `note_id`.
        """
        readme = NoteEntity(
            note_id=UNDEFINED,
            title=README_TITLE,
            content="",
            author_id=user_ctx.user_id,
            updated_at=datetime.now(),
            embeddings=[],
            permissions=UNDEFINED,
            parent_dir_id=directory_id,
        )
        inserted = await self._note_repo.insert(readme, user_ctx)
        if inserted.note_id not in (UNDEFINED, None):
            await self._bind_readme(directory_id, str(inserted.note_id))
        return inserted

    async def _bind_readme(self, directory_id: str, readme_note_id: str) -> None:
        """Persist `readme_note_id` and link the note to the directory.

        Writes the Postgres pointer plus the SpiceDB
        ``note#parent_directory@directory`` relation so both lookups
        and permission checks see the binding.  Idempotent across
        repeat binds.
        """
        await self._directory_repo.update_directory(
            DirectoryEntity(
                id=directory_id,
                readme_note_id=readme_note_id,
            )
        )
        await self._ensure_readme_parent_directory_relation(
            directory_id=directory_id,
            readme_note_id=readme_note_id,
        )

    async def _ensure_readme_parent_directory_relation(
        self,
        directory_id: str,
        readme_note_id: str,
    ) -> None:
        """Insert the ``note#parent_directory@directory`` relation if missing.

        Skips the insert when an existing relation already points at
        `directory_id`, keeping the bind idempotent.
        """
        existing = await self._permission_repo.lookup_relationships(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, readme_note_id),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, directory_id),
            )
        )
        if existing:
            return
        relation = Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, readme_note_id),
            relation=NoteRelationEnum.PARENT_DIRECTORY,
            subject=SubjectRef(ObjectTypeEnum.DIRECTORY, directory_id),
        )
        await self._permission_repo.insert([relation])

    async def _apply_readme_overrides(
        self,
        directory: DirectoryEntity,
        user_ctx: UserContextABC,
    ) -> None:
        """Overlay parsed README fields onto `directory` in place.

        Fetches the linked README note and uses
        :func:`~src.utils.readme_parser.parse_readme` to overwrite
        `image_url` and `description`.  Falls back silently when the
        note is missing or has no parseable content.
        """
        readme_id = directory.readme_note_id
        if readme_id in (UNDEFINED, None):
            return
        readme = await self._note_repo.select_by_id(str(readme_id), user_ctx)
        if readme is None:
            return
        parsed = parse_readme(
            readme.content if readme.content is not UNDEFINED else None
        )
        if parsed.image_url is not None:
            directory.image_url = parsed.image_url
        if parsed.description:
            directory.description = parsed.description


__all__ = ["DirectoryService", "README_TITLE"]
