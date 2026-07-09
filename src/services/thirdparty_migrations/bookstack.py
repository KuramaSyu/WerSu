"""Concrete :class:`ThirdpartyMigrationsServiceABC` for BookStack imports.

Implements the four-step BookStack book import the user spec calls out:

    1. upload all attachments from ``files/`` and build an
       old-id -> new-key map;
    2. upload the book itself as a directory (with the cover image
       stored in ``image_url`` so the auto-generated README picks it up);
    3. upload each chapter as a directory and link it to the book
       directory via the ``parent`` relation;
    4. upload each page as a note (linking attachments that are
       referenced inside the note content) and create a second
       rewrite pass over ``[[bsexport:...]]`` cross-refs.

The implementation is best-effort: per-page failures are logged and
the migration continues so one bad page does not abort the whole
import.  The caller (the gRPC adapter) decides how to surface the
partial result.
"""

from __future__ import annotations

import mimetypes
from typing import Callable, Dict, List

from src.api import DirectoryServiceABC, LoggingProvider, NoteServiceABC, UserContextABC
from src.api.undefined import UNDEFINED
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.attachments.attachments import Attachment
from src.services.attachments import AttachmentFacadeABC
from src.services.thirdparty_migrations import (
    ImportedChapter,
    MigrationResult,
    ThirdpartyMigrationsServiceABC,
)
from src.services.thirdparty_migrations._attachment_url import build_attachment_url
from src.services.thirdparty_migrations.bookstack_html_converter import (
    BookstackHtmlConverter,
)
from src.services.thirdparty_migrations.bookstack_models import (
    BookstackBook,
    BookstackPage,
)
from src.services.thirdparty_migrations.bookstack_reader import (
    BookstackBookReader,
    BookstackZipError,
)
from src.utils.extract_attachments import extract_attachment_ids


class BookstackBookImport(ThirdpartyMigrationsServiceABC):
    """Import a BookStack portable book zip into the project.

    Args:
        attachment_facade: used to upload files to S3 / metadata to
            Postgres and to link attachments to notes.
        directory_service: used to create the book and chapter
            directories (this layer also writes the parent /
            admin relations and the auto-generated README).
        note_service: used to create the page notes; this layer
            wires the parent-directory relation and logs activity.
        attachment_url_builder: turns an attachment key into the URL
            that should replace an ``files/<filename>`` reference in
            converted note content.  Defaults to
            :func:`build_attachment_url`.
        log: project logging provider.
        reader: optional :class:`BookstackBookReader` override (used
            by tests).
        converter: optional :class:`BookstackHtmlConverter` override
            (used by tests).
    """

    def __init__(
        self,
        attachment_facade: AttachmentFacadeABC,
        directory_service: DirectoryServiceABC,
        note_service: NoteServiceABC,
        *,
        attachment_url_builder: Callable[[str], str] = build_attachment_url,
        log: LoggingProvider,
        reader: BookstackBookReader | None = None,
        converter: BookstackHtmlConverter | None = None,
    ) -> None:
        self._attachment_facade = attachment_facade
        self._directory_service = directory_service
        self._note_service = note_service
        self._url_builder = attachment_url_builder
        self.log = log(__name__, self)
        self._reader = reader or BookstackBookReader()
        self._converter = converter or BookstackHtmlConverter(
            attachment_url_builder=attachment_url_builder
        )

    async def migrate(
        self,
        content: bytes,
        user_ctx: UserContextABC,
    ) -> MigrationResult:
        book = self._reader.read(content)
        return await self._import_book(book, user_ctx)

    async def _import_book(
        self,
        book: BookstackBook,
        user_ctx: UserContextABC,
    ) -> MigrationResult:
        # 1. Upload every file referenced by the export, recording
        #    the new attachment key so we can rewrite inline refs.
        file_index, id_index, attachments_uploaded = await self._upload_attachments(
            book, user_ctx
        )

        # 2. Create the book directory.  The cover is stored in
        #    image_url, which the auto-generated README picks up.
        book_dir = await self._create_book_directory(book, file_index, user_ctx)

        # 3. Create chapter directories under the book directory.
        chapter_dirs: Dict[int, str] = {}
        chapters: List[ImportedChapter] = []
        for chapter in book.chapters:
            try:
                chapter_dir = await self._directory_service.create_directory(
                    DirectoryEntity(
                        name=chapter.name,
                        display_name=chapter.name,
                        description=self._converter.html_to_markdown(
                            chapter.description_html
                        ),
                        image_url=UNDEFINED,
                        parent_id=str(book_dir.id),
                    ),
                    user_ctx,
                )
            except Exception as exc:
                self.log.error(
                    "failed to create chapter directory %r: %s", chapter.name, exc
                )
                continue
            chapter_dirs[chapter.id] = str(chapter_dir.id)
            chapters.append(
                ImportedChapter(
                    directory_id=str(chapter_dir.id),
                    chapter_name=chapter.name,
                    pages_imported=0,
                )
            )

        # 4. Two passes over pages so we can rewrite [[bsexport:...]]
        #    cross-refs once every old id has a known target.
        first_pass: Dict[int, NoteEntity] = {}
        pages_imported = 0
        all_pages = self._collect_pages(book)
        for page in all_pages:
            try:
                note = await self._create_page_note(page, chapter_dirs, book_dir, file_index, user_ctx)
            except Exception as exc:
                self.log.error("failed to import page %r: %s", page.name, exc)
                continue
            first_pass[page.id] = note
            pages_imported += 1

        # Second pass: link attachments referenced in each note's
        # content + handle page.images[] explicit entries.
        for page in all_pages:
            note = first_pass.get(page.id)
            if note is None:
                continue
            await self._link_attachments_for_note(page, note, file_index, id_index, user_ctx)

        # Rewrite cross-references in the inserted notes.  Page /
        # chapter / book kinds resolve to note / directory ids that
        # we now have; image / attachment kinds resolve via the
        # id_index map.
        await self._rewrite_cross_references(first_pass, id_index, user_ctx)

        return MigrationResult(
            root_directory_id=str(book_dir.id),
            pages_imported=pages_imported,
            attachments_uploaded=attachments_uploaded,
            chapters=chapters,
        )

    async def _upload_attachments(
        self,
        book: BookstackBook,
        user_ctx: UserContextABC,
    ) -> tuple[Dict[str, str], Dict[str, Dict[int, str]], int]:
        """Upload every file under ``files/`` and build rewrite maps.

        Returns:
            file_index: filename -> new attachment key.
            id_index: cross-ref kind -> source id -> new attachment key.
            attachments_uploaded: total count of successful uploads.
        """
        file_index: Dict[str, str] = {}
        id_index: Dict[str, Dict[int, str]] = {
            "image": {},
            "attachment": {},
        }
        uploaded = 0
        for filename, data in book.files.items():
            try:
                attachment = Attachment(
                    key=UNDEFINED,
                    filename=filename,
                    filepath=UNDEFINED,
                    content_type=_guess_content_type(filename),
                    size=len(data),
                    content=data,
                )
                stored = await self._attachment_facade.post_attachment(
                    attachment, user_ctx
                )
            except Exception as exc:
                self.log.error("failed to upload attachment %r: %s", filename, exc)
                continue
            key = str(stored.key)
            file_index[filename] = key
            uploaded += 1

        # Build id_index entries from explicit image / attachment
        # objects on each page; we walk pages and chapters.
        for page in self._collect_pages(book):
            for img in page.images:
                if img.id and img.file in file_index:
                    id_index["image"][img.id] = file_index[img.file]
            for att in page.attachments:
                if att.id and att.file in file_index:
                    id_index["attachment"][att.id] = file_index[att.file]
        return file_index, id_index, uploaded

    async def _create_book_directory(
        self,
        book: BookstackBook,
        file_index: Dict[str, str],
        user_ctx: UserContextABC,
    ) -> DirectoryEntity:
        cover_url = UNDEFINED
        if book.cover and book.cover in file_index:
            cover_url = self._url_builder(file_index[book.cover])

        description = self._converter.html_to_markdown(book.description_html)
        return await self._directory_service.create_directory(
            DirectoryEntity(
                name=book.name,
                display_name=book.name,
                description=description,
                image_url=cover_url,
                parent_id=None,
            ),
            user_ctx,
        )

    async def _create_page_note(
        self,
        page: BookstackPage,
        chapter_dirs: Dict[int, str],
        book_dir: DirectoryEntity,
        file_index: Dict[str, str],
        user_ctx: UserContextABC,
    ) -> NoteEntity:
        parent_dir_id = chapter_dirs.get(page.chapter_id or -1) or str(book_dir.id)
        body = self._converter.convert_content(page, file_index)
        return await self._note_service.insert_note(
            NoteEntity(
                title=page.name,
                content=body,
                author_id=user_ctx.user_id,
                parent_dir_id=parent_dir_id,
            ),
            user_ctx,
        )

    async def _link_attachments_for_note(
        self,
        page: BookstackPage,
        note: NoteEntity,
        file_index: Dict[str, str],
        id_index: Dict[str, Dict[int, str]],
        user_ctx: UserContextABC,
    ) -> None:
        note_id = str(note.note_id)
        content = note.content or ""

        # (a) Inline refs inside the note body -- use the existing
        # extract_attachment_ids helper so the same URL format the
        # HTML converter writes is picked up.
        referenced: set[str] = set()
        for key in extract_attachment_ids(content):
            referenced.add(key)

        # (b) Explicit image entries declared on the page.
        for img in page.images:
            if img.file in file_index:
                referenced.add(file_index[img.file])

        for key in referenced:
            try:
                await self._attachment_facade.link_attachment_to_note(
                    key, note_id, user_ctx
                )
            except Exception as exc:
                self.log.warning(
                    "link_attachment_to_note failed key=%s note=%s: %s",
                    key,
                    note_id,
                    exc,
                )

    async def _rewrite_cross_references(
        self,
        first_pass: Dict[int, NoteEntity],
        id_index: Dict[str, Dict[int, str]],
        user_ctx: UserContextABC,
    ) -> None:
        if not first_pass:
            return
        for page_id, note in first_pass.items():
            original = note.content or ""
            rewritten = self._converter.rewrite_cross_references(original, id_index)
            if rewritten == original:
                continue
            try:
                note.content = rewritten
                await self._note_service.update_note(note, user_ctx)
            except Exception as exc:
                self.log.warning(
                    "cross-ref rewrite failed for note %s: %s", note.note_id, exc
                )

    @staticmethod
    def _collect_pages(book: BookstackBook) -> List[BookstackPage]:
        pages: List[BookstackPage] = []
        for chapter in book.chapters:
            pages.extend(chapter.pages)
        pages.extend(book.pages)
        return pages


__all__ = ["BookstackBookImport"]


def _guess_content_type(filename: str) -> str:
    """Best-effort content type lookup that falls back to octet-stream."""
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"