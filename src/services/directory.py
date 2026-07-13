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
from typing import TYPE_CHECKING, List, Optional

from src.api import (
    ActivityLoggerServiceABC,
    DirectoryIncludeOptions,
    DirectoryRelationEnum,
    DirectoryServiceABC,
    LoggingProvider,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    PermissionRepoABC,
    Relationship,
    SubjectRef,
)
from src.api.note_service import NoteServiceABC
from src.api.permission_repo import DirectoryChild
from src.api.undefined import UNDEFINED, unwrap_undefined, unwrap_undefined_or
from src.api.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.directory.directory import DirectoryFacade
from src.api.note_facade import NoteRepoFacadeABC
from src.domain.permission_chain import (
    HasDirectoryDeletePerm,
    HasDirectoryViewPerm,
    HasDirectoryWritePerm,
)
from src.utils.attachment_url import build_attachment_url
from src.utils.extract_attachments import extract_attachment_ids
from src.utils.readme_parser import ParsedReadme, parse_readme


if TYPE_CHECKING:
    from src.services.attachments import AttachmentFacadeABC


README_TITLE = "README.md"
"""Title of the auto-managed ``README.md`` note for each directory."""


class DirectoryService(DirectoryServiceABC):
    """Concrete :class:`~src.api.directory_service.DirectoryServiceABC`.

    Every public method gates the call with a permission check from
    :mod:`src.domain.permission_chain` before it touches the underlying
    repos.  The README bookkeeping for
    :meth:`get_directory_notes` is also performed exclusively here.

    Recursive delete + :meth:`dry_delete` + the README attachment
    workflow require an :class:`AttachmentFacadeABC` and a
    :class:`NoteService` so the service can fan the cascade out to
    attachment rows and notes.  All collaborators are required --
    pass real instances wired by :func:`src.main.build`.
    """

    def __init__(
        self,
        directory_repo: DirectoryFacade,
        note_repo: NoteRepoFacadeABC,
        permission_repo: PermissionRepoABC,
        activity_logger: ActivityLoggerServiceABC,
        note_service: NoteServiceABC,
        attachment_facade: AttachmentFacadeABC,
        log: LoggingProvider,
    ) -> None:
        self._directory_repo = directory_repo
        self._note_repo = note_repo
        self._permission_repo = permission_repo
        self._activity_logger = activity_logger
        self._note_service = note_service
        self._attachment_facade = attachment_facade
        self.log = log(__name__, self)

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

        note_ids = await self._permission_repo.lookup(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, UNDEFINED),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, directory_id),
            )
        )

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
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> Optional[DirectoryEntity]:
        """Return a single directory visible to `user_ctx`.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.get_directory`.

        Note:
            Overlays the parsed README note onto the returned
            :class:`~src.db.entities.directory.directory.DirectoryEntity`
            when the directory has a `readme_note_id`.  Direct child
            counts are derived from `len(directory.child_directory_ids)`
            and `len(directory.child_note_ids)` when those lists
            are fetched; the entity carries no count fields.
        """
        await self._assert_directory_view(directory_id, user_ctx)
        directory = await self._directory_repo.fetch_directory(
            directory_id, include=include,
        )
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
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> List[DirectoryEntity]:
        """Return all directories visible to `user_ctx`, paginated.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.get_directories`.

        Note:
            Overlays each directory's parsed README note before
            pagination so a linked ``README.md`` drives the
            displayed `image_url` / `description`.  Direct child
            counts are derived from `len(directory.child_directory_ids)`
            and `len(directory.child_note_ids)` when those lists
            are fetched; the entity carries no count fields.
        """
        if limit and limit < 0:
            raise ValueError("limit must be >= 0")
        if offset and offset < 0:
            raise ValueError("offset must be >= 0")

        directory_ids = await self._directory_repo.list_user_directory_ids(user_ctx)
        directories: List[DirectoryEntity] = []
        for directory_id in directory_ids:
            directory = await self._directory_repo.fetch_directory(
                directory_id, include=include,
            )
            if directory:
                directories.append(directory)

        if parent_id:
            directories = [
                d
                for d in directories
                if d.parent_directory_ids not in (UNDEFINED, None)
                and parent_id in {
                    str(p) for p in (d.parent_directory_ids or []) if p
                }
            ]
        for directory in directories:
            await self._apply_readme_overrides(directory, user_ctx)

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
        # one or more parent directories, gate creation on a write
        # check against every parent so random callers can't nest
        # directories wherever they want.
        parent_ids = entity.parent_directory_ids
        if parent_ids:
            for parent_id in parent_ids:
                check = HasDirectoryWritePerm(
                    str(parent_id)
                ).set_permission_repo(self._permission_repo)
                result = await check.check(user_ctx)
                if result.error:
                    raise result.error

        created_dir = await self._directory_repo.create_directory(
            DirectoryEntity(
                id=entity.id,
                slug=entity.slug,
                display_name=entity.display_name,
                description=entity.description,
                image_url=entity.image_url,
                parent_directory_ids=entity.parent_directory_ids,
                tag_ids=entity.tag_ids,
                readme_note_id=entity.readme_note_id,
                relations=UNDEFINED,  # dir#admin@user will be created in the facade
            ), user_ctx
        )

        # Create the README.md which contains description and image of directory
        # then also insert note#parent_directory@directory relation 
        if created_dir.id:
            existing_readme_id = (
                str(created_dir.readme_note_id)
                if created_dir.readme_note_id
                else None
            )
            readme: Optional[NoteEntity] = None
            if not existing_readme_id:
                readme = await self._create_readme(
                    str(created_dir.id), 
                    user_ctx,

                )
            else:
                await self._bind_readme(
                    str(created_dir.id), existing_readme_id
                )
            
            # update readme with given image url.
            image_urls = extract_attachment_ids(created_dir.image_url or "")
            if image_urls and created_dir.image_url:
                attachment_key = image_urls[0]
                readme = readme or await self._note_repo.select_by_id(str(existing_readme_id), user_ctx)
                if not readme:
                    self.log.warning(f"failed to fetch README note for directory {created_dir.id} to link attachment {attachment_key}")
                else:
                    await self._update_readme(readme, user_ctx, attachment_key=attachment_key)

            refreshed = await self._directory_repo.fetch_directory(
                str(created_dir.id)
            )
            if refreshed is not None:
                created_dir = refreshed
            await self._activity_logger.directory_created(
                str(created_dir.id), user_ctx
            )
        return created_dir

    async def patch_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: UserContextABC,
    ) -> Optional[DirectoryEntity]:
        """Patch an existing directory.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.patch_directory`.
        """
        if not entity.id:
            raise ValueError("id is required for patch_directory")

        check = HasDirectoryWritePerm(str(entity.id)).set_permission_repo(
            self._permission_repo
        )
        result = await check.check(user_ctx)
        if result.error:
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
        """Delete a directory and every exclusively-owned child.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.delete_directory`.

        Resolves the subtree via
        :meth:`PermissionRepoABC.resolve_children`, then deletes
        every sub-directory (recursively, through this method),
        every note whose only parent sits inside the subtree, and
        every attachment whose only parent note sits inside the
        subtree.  Finally removes the directory row itself.
        """
        check = HasDirectoryDeletePerm(directory_id).set_permission_repo(
            self._permission_repo
        )
        result = await check.check(user_ctx)
        if result.error:
            raise result.error

        children = await self._resolve_children(directory_id, user_ctx)

        # Delete attachments first so the parent_note rows in
        # SpiceDB drop before their notes do.  The repos do not
        # cascade-delete relations on their own, so the order
        # matters when something later in the call expects the
        # attachment to be gone.
        if children.attachment_ids:
            await self._delete_attachments(children.attachment_ids, user_ctx)

        # Delete notes next, before their parent directories go
        # away, so the service can still look them up by id.
        if children.note_ids:
            await self._delete_notes(children.note_ids, user_ctx)

        # Sub-directories last -- they recurse, so each one runs
        # through this method again with its own delete permission
        # check.
        for sub_id in children.sub_directory_ids:
            if sub_id == directory_id:
                continue
            await self.delete_directory(sub_id, user_ctx)

        deleted = await self._directory_repo.delete_directory(
            DirectoryEntity(id=directory_id)
        )
        if deleted:
            await self._activity_logger.directory_deleted(
                directory_id, user_ctx
            )
        return deleted

    async def dry_delete(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> List[DirectoryChild]:
        """Resolve the exclusive subtree without deleting anything.

        See :meth:`~src.api.directory_service.DirectoryServiceABC.dry_delete`.
        """
        await self._assert_directory_view(directory_id, user_ctx)

        children = await self._resolve_children(directory_id, user_ctx)

        result: List[DirectoryChild] = []
        for sub_id in children.sub_directory_ids:
            if sub_id == directory_id:
                continue
            directory = await self._directory_repo.fetch_directory(sub_id)
            slug = (
                str(directory.slug)
                if directory is not None and directory.slug not in (UNDEFINED, None)
                else sub_id
            )
            result.append(DirectoryChild(id=sub_id, kind="directory", name=slug))

        for note_id in children.note_ids:
            note = await self._note_repo.select_by_id(note_id, user_ctx)
            title = (
                str(note.title)
                if note is not None and note.title not in (UNDEFINED, None)
                else note_id
            )
            result.append(DirectoryChild(id=note_id, kind="note", name=title))

        # traverse all attachments to provide the filename instead of the key as name
        for attachment_key in children.attachment_ids:
            name = attachment_key
            try:
                metadata = await self._attachment_facade.get_metadata(
                    attachment_key, user_ctx
                )
                name = metadata.filename or attachment_key
            except Exception:
                pass

            result.append(
                DirectoryChild(id=attachment_key, kind="attachment", name=name)
            )

        # Sort by kind (directories, then notes, then attachments)
        # then by id so the output is deterministic for tests and
        # UI confirmation prompts.
        kind_order = {"directory": 0, "note": 1, "attachment": 2}
        result.sort(key=lambda c: (kind_order.get(c.kind, 99), c.id))
        return result

    async def _resolve_children(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ):
        return await self._permission_repo.resolve_children(
            directory_id,
            max_depth=10,
            exclusive=True,
        )

    async def _delete_notes(
        self,
        note_ids: List[str],
        user_ctx: UserContextABC,
    ) -> None:
        for note_id in note_ids:
            try:
                await self._note_service.delete_note(note_id, user_ctx)
            except Exception as exc:
                # best-effort: log + continue so a single note does
                # not abort the whole cascade.
                self.log.warning(
                    "delete_note failed during cascade for %s: %s", note_id, exc
                )

    async def _delete_attachments(
        self,
        attachment_keys: List[str],
        user_ctx: UserContextABC,
    ) -> None:
        for key in attachment_keys:
            try:
                await self._attachment_facade.delete_attachment(key, user_ctx)
            except Exception as exc:
                self.log.warning(
                    "delete_attachment failed during cascade for %s: %s", key, exc
                )

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
        if result.error:
            raise result.error
        
    async def _update_readme(
        self,
        readme: NoteEntity,
        user_ctx: UserContextABC,
        attachment_key: Optional[str] = None,
    ) -> NoteEntity:
        """Updates the README and links the attachment if provided. """
        # now we have the ID -> link the attachment key to the note
        if attachment_key:
            await self._attachment_facade.link_attachment_to_note(
                attachment_key, unwrap_undefined(readme.note_id), user_ctx
            )

        # miss use ParseReadme to create one
        title = readme.title or "README.md"
        content = readme.content or ""
        ParsedReadme(
            title=title,
            description=content,
            image_url=build_attachment_url(attachment_key) if attachment_key else None,
        )

        # update the readme if any of the fields are provided
        if readme.title or readme.content or attachment_key:
            content = ParsedReadme(
                title=title,
                description=content,
                image_url=build_attachment_url(attachment_key) if attachment_key else None,
            ).write_readme()
            return await self._note_service.update_note(readme, user_ctx)
        return readme


    async def _create_readme(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> NoteEntity:
        """Create a fresh ``README.md`` for `directory_id` and bind it.

        Inserts a README note owned by `user_ctx`, then binds
        it to the directory via :meth:`_bind_readme`. It 

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
            directory_ids=[directory_id],
        )
        inserted = await self._note_repo.insert(readme, user_ctx)

        if inserted.note_id:
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
        if not readme:
            return
        parsed = parse_readme(
            unwrap_undefined_or(readme.content, None),
        )
        if parsed.image_url is not None:
            directory.image_url = parsed.image_url
        if parsed.description:
            directory.description = parsed.description


__all__ = ["DirectoryService", "README_TITLE"]
