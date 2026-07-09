# BookStack Book Import

This document describes how a BookStack portable book zip
(`data.json` + `files/`) is imported into the WerSu gRPC service.

Currently only the BookStack source is implemented.  Future sources
(Notion, Confluence, Obsidian) will be added as additional
implementations of `:class:`ThirdpartyMigrationsServiceABC`` against
the same proto.

## Components

- **Frontend** / **REST-Proxy** — the browser talks to the REST Proxy,
  which forwards the request to WerSu-gRPC.
- **WerSu-gRPC** — hosts the new
  `ThirdpartyMigrationsService` gRPC service.  Its
  `BookstackBookImport` RPC is client-streaming: the client streams the
  zip bytes in 1 MB chunks.
- **BookstackBookImport** — the application service in
  `src/services/thirdparty_migrations/bookstack.py`.  Orchestrates the
  four-step pipeline and delegates persistence to the existing
  `DirectoryService`, `NoteService` and `AttachmentFacade`.
- **SpiceDB** — permissions; receives `note#parent_directory`,
  `directory#parent` and the auto-granted `directory#admin` relations.
- **Postgres** — note and directory rows.
- **Garage (S3)** — attachment bytes.

## Class diagram

```mermaid
classDiagram
    class ThirdpartyMigrationsServiceABC {
        <<abstract>>
        +migrate(content, user_ctx) MigrationResult
    }
    class BookstackBookImport {
        +migrate(content, user_ctx) MigrationResult
        -_upload_attachments(book, user_ctx)
        -_create_book_directory(book, file_index, user_ctx)
        -_create_page_note(page, chapter_dirs, book_dir, file_index, user_ctx)
        -_link_attachments_for_note(page, note, file_index, id_index, user_ctx)
        -_rewrite_cross_references(first_pass, id_index, user_ctx)
        -_collect_pages(book) List~BookstackPage~
    }
    class BookstackBookReader {
        +read(content) BookstackBook
        +DATA_FILENAME$
        +FILES_PREFIX$
    }
    class BookstackHtmlConverter {
        +html_to_markdown(html) str
        +convert_content(page, file_index) str
        +rewrite_image_sources(content, file_index) str
        +rewrite_cross_references(content, id_index) str
    }
    class GrpcThirdpartyMigrationsService {
        +BookstackBookImport(request_iterator, context) Response
        -_consume_stream(request_iterator) tuple
        -_to_response(result) Response
        -_set_context_error(exc, context)
    }
    class DirectoryServiceABC {
        <<interface>>
        +create_directory(entity, user_ctx)
    }
    class NoteServiceABC {
        <<interface>>
        +insert_note(note, user_ctx)
        +update_note(note, user_ctx)
    }
    class AttachmentFacadeABC {
        <<interface>>
        +post_attachment(attachment, user_ctx)
        +link_attachment_to_note(key, note_id, user_ctx)
    }
    class BookstackBook {
        +name
        +id
        +description_html
        +cover
        +chapters
        +pages
        +files
    }
    class BookstackChapter {
        +id
        +name
        +description_html
        +pages
        +priority
    }
    class BookstackPage {
        +id
        +name
        +html
        +markdown
        +chapter_id
        +images
        +attachments
    }
    class MigrationResult {
        +root_directory_id
        +pages_imported
        +attachments_uploaded
        +chapters
    }
    class ImportedChapter {
        +directory_id
        +chapter_name
        +pages_imported
    }

    ThirdpartyMigrationsServiceABC <|-- BookstackBookImport
    GrpcThirdpartyMigrationsService ..> ThirdpartyMigrationsServiceABC : delegates to
    BookstackBookImport --> DirectoryServiceABC
    BookstackBookImport --> NoteServiceABC
    BookstackBookImport --> AttachmentFacadeABC
    BookstackBookImport --> BookstackBookReader
    BookstackBookImport --> BookstackHtmlConverter
    BookstackBookReader --> BookstackBook
    BookstackBook "1" --> "*" BookstackChapter
    BookstackBook "1" --> "*" BookstackPage : direct child pages
    BookstackChapter "1" --> "*" BookstackPage
    ThirdpartyMigrationsServiceABC ..> MigrationResult : returns
    MigrationResult "1" --> "*" ImportedChapter
```

`GrpcThirdpartyMigrationsService` is intentionally thin.  It only
reassembles the streamed zip bytes, builds the `user_ctx` via
`RepoContextFactory`, and maps service exceptions to gRPC status
codes.  All the real work is done by `:class:`BookstackBookImport``,
which composes the three existing services (`DirectoryService`,
`NoteService`, `AttachmentFacade`) plus the two parsers
(`BookstackBookReader`, `BookstackHtmlConverter`).

## Sequence diagram

```mermaid
sequenceDiagram
    autonumber
    actor Client as REST-Proxy
    participant gRPC as GrpcThirdpartyMigrationsService
    participant Import as BookstackBookImport
    participant Reader as BookstackBookReader
    participant Att as AttachmentFacade
    participant Dir as DirectoryService
    participant Notes as NoteService
    participant DB as Postgres + SpiceDB
    participant S3 as Garage (S3)

    Client->>gRPC: stream BookstackBookImportChunk (user_id + bytes)

    Note over gRPC: Reassemble zip bytes<br>from the chunk stream
    gRPC->>Import: migrate(content, user_ctx)

    Import->>Reader: read(content)
    Reader-->>Import: BookstackBook (chapters, pages, files)

    Note over Import: Step 1 - upload all files
    loop for each file in book.files
        Import->>Att: post_attachment(Attachment(filename, content, ...))
        Att->>S3: PUT object
        S3-->>Att: ok
        Att->>DB: insert metadata row
        Att-->>Import: stored Attachment (with new key)
    end

    Note over Import: Step 2 - create the book directory
    Import->>Dir: create_directory(DirectoryEntity(name, image_url=cover_url))
    Dir->>DB: insert directory row + parent + admin relations
    Dir-->>Import: book_dir (with id, auto README)

    Note over Import: Step 3 - create chapter directories
    loop for each chapter in book.chapters (sorted by priority)
        Import->>Dir: create_directory(name, parent_id=book_dir.id)
        Dir->>DB: insert row + parent relation
        Dir-->>Import: chapter_dir
    end

    Note over Import: Step 4 - first pass: insert notes
    loop for each page (chapter pages + direct child pages)
        Import->>Import: BookstackHtmlConverter.convert_content(page, file_index)
        Import->>Notes: insert_note(NoteEntity(title, content, parent_dir_id))
        Notes->>DB: insert note + parent_directory + owner relations
        Notes-->>Import: note (with id)
    end

    Note over Import: Step 5 - link attachments to notes
    loop for each inserted note
        Import->>Att: link_attachment_to_note(key, note_id, user_ctx)
        Att->>DB: insert note.attachment_note_link row + attachment#parent_note relation
    end

    Note over Import: Step 6 - rewrite [[bsexport:...]] cross-refs
    loop for each inserted note
        opt content changed
            Import->>Notes: update_note(note)
            Notes->>DB: update note row + re-extract attachment refs
        end
    end

    Import-->>gRPC: MigrationResult(root_directory_id, pages_imported, ...)
    gRPC-->>Client: BookstackBookImportResponse
```

Step-by-step:

1. The REST-Proxy streams the zip bytes into the gRPC RPC as a
   series of `BookstackBookImportChunk` messages.  The first chunk
   carries the `user_id`; later chunks may leave it empty.
2. `GrpcThirdpartyMigrationsService` reassembles the bytes and calls
   `BookstackBookImport.migrate(content, user_ctx)`.
3. `BookstackBookReader` parses the zip: it pulls `data.json` for the
   chapter / page tree and loads every binary under `files/` into
   `book.files` (a filename -> bytes dict).
4. **Step 1 - upload all files.**  Each file becomes an
   `Attachment` via `AttachmentFacade.post_attachment`, which writes
   bytes to Garage (S3) and metadata to Postgres.  The new attachment
   key is recorded in `file_index: dict[filename -> key]`.  An
   `id_index` is also built from the explicit `images[]` /
   `attachments[]` entries on each page so that `[[bsexport:image:N]]`
   cross-refs can be rewritten in step 6.
5. **Step 2 - create the book directory.**  The cover image (if any)
   is uploaded with the rest, wrapped in an `/api/attachments/image?`
   URL via `build_attachment_url`, and stored on the new directory's
   `image_url`.  `DirectoryService.create_directory` auto-grants the
   caller `admin`, auto-creates the README note, and (because
   `parent_id` is left as `None`) places the book at the top level.
6. **Step 3 - create chapter directories.**  For each chapter (sorted
   by `priority`) the importer calls
   `create_directory(name, parent_id=book_dir.id)`.  This writes the
   `directory#parent` relation, so chapter directories are nested
   under the book directory.
7. **Step 4 - first pass: insert notes.**  For every page (chapter
   pages and direct child pages, sorted by `priority`),
   `BookstackHtmlConverter.convert_content` picks `page.markdown` when
   non-empty, otherwise runs `html2text` on `page.html`, and rewrites
   every `<img src="filename">` / `![alt](filename)` reference to the
   new attachment URL.  The note is inserted via
   `NoteService.insert_note`, which writes the `parent_directory`
   relation and the `owner` relation.  Direct child pages go under
   the book directory; pages inside a chapter go under that chapter's
   directory.
8. **Step 5 - link attachments to notes.**  For every inserted note,
   `extract_attachment_ids(note.content)` is called to find every
   `/api/attachments/image?key=...` URL inline, and every image in
   `page.images[]` is added on top.  Each unique key is linked via
   `AttachmentFacade.link_attachment_to_note`, which inserts both a
   `note.attachment_note_link` row and the
   `attachment#parent_note@note` relation.
9. **Step 6 - rewrite cross-refs.**  BookStack pages may contain
   `[[bsexport:image:N]]` / `[[bsexport:attachment:N]]` cross-refs.
   After all notes are inserted and every old id has a known
   `id_index` target, each note's content is rewritten and persisted
   via `NoteService.update_note`.  `update_note` re-extracts
   attachment refs from the new content, so this single step also
   handles the case where a rewritten cross-ref introduced a fresh
   attachment URL that should now be linked.
10. The service returns `MigrationResult(book_directory_id,
    pages_imported, attachments_uploaded, chapters)`, which the
    gRPC adapter maps to `BookstackBookImportResponse`.

## Error handling

The whole pipeline is **best-effort**.  Per-page failures (insert,
update) and per-attachment failures (upload, link) are logged and
skipped so that one bad page does not abort the import.  Only the
following errors short-circuit the request:

- `BookstackZipError` (raised by the reader when the bytes are not a
  zip, are missing `data.json`, or do not contain a `book` key) ->
  mapped to gRPC `INVALID_ARGUMENT`.
- `PermissionError` (raised by any of the three downstream services)
  -> mapped to `PERMISSION_DENIED`.
- Empty / missing `user_id` or empty content on the streamed RPC ->
  `INVALID_ARGUMENT`.

Unknown errors fall through to `INTERNAL` so the gRPC client gets a
proper status code instead of a hang.

## Directory naming

The orchestrator sets `display_name=chapter.name` on every chapter
directory and `display_name=book.name` on the book directory.  The
frontend renders the directory name from the `display_name` column,
so leaving it as `UNDEFINED` would show the chapter as blank in the
UI.  The orchestrator-level fix is what matters; the auto-README
note still does not override the display name (it only sets the
description and image).