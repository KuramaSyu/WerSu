"""Pinned tests for the BookStack converter around a real emergency-backup page.

The original "Emergency List: Vueko Backup with duply and rclone"
BookStack page tripped two regressions in the converter:

1. ``<pre><code class="language-...">`` code blocks were rendered
   as 4-space indented blocks by :mod:`html2text`; the project's
   markdown viewer did not recognise those as code blocks, so
   every code block in the imported note looked like an empty
   fence.
2. ``<details><summary>...</summary>...</details>`` collapsible
   sections lost their wrapper; the body of each section was
   emitted as plain text immediately below the summary, so the
   content was no longer inside the collapsible region.

These tests pin the new behaviour against the anonymised
Hunter-x-Hunter re-write of the page in
:mod:`tests.fixtures.bookstack_emergency_backup`.  The re-write
keeps the original structure (one intro code block + three
``<details>`` sections, two of which themselves contain code
blocks) but replaces the personal paths / hostnames / usernames
with names from the series, so the fixture is safe to commit
and re-run.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from typing import List, Optional, Tuple

import pytest

from src.api.services.directory_service import DirectoryServiceABC
from src.api.services.note_service import NoteServiceABC
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.attachments.attachments import Attachment
from src.services.attachment_facade import AttachmentFacadeABC
from src.services.thirdparty_migrations.bookstack import BookstackBookImport
from src.services.thirdparty_migrations.html_converter import (
    BookstackHtmlConverter,
    ConvertOptions,
)
from src.services.thirdparty_migrations.bookstack_models import (
    BookstackPage,
)
from tests._fixtures_pkg.bookstack_emergency_backup import (
    EMERGENCY_BACKUP_BOOK_PAYLOAD,
    EMERGENCY_BACKUP_PAGE_HTML,
)
from tests.stubs.directory_service import _StubDirectoryService
from tests.stubs.logging import silent_logger
from tests.stubs.user_context import _UserContext


def _builder(key: str) -> str:
    return f"/u/{key}"


def _converter(
    *, convert_details: bool = False
) -> BookstackHtmlConverter:
    return BookstackHtmlConverter(
        attachment_url_builder=_builder,
        convert_details=convert_details,
    )


# ---- ConvertOptions TypedDict ------------------------------------------


def test_convert_options_is_a_typed_dict_with_convert_details_key() -> None:
    """`ConvertOptions` exposes only `convert_details` and is a
    `TypedDict` (so callers can pass a literal ``{"convert_details": True}``
    without subclassing)."""
    annotations = ConvertOptions.__annotations__
    assert "convert_details" in annotations
    # `TypedDict` instances expose `_special_keys` so they can be
    # used as ``**options`` kwargs without re-validating keys.
    assert hasattr(ConvertOptions, "__total__")


# ---- default behaviour -------------------------------------------------


def test_default_emits_fenced_code_block_with_language_hint() -> None:
    """`html_to_markdown` always converts ``<pre><code class="language-X">``
    to a fenced ```` ```X ```` block, not a 4-space indented block --
    some markdown viewers drop the indented form and render the
    code region as an empty fence."""
    md = _converter().html_to_markdown(EMERGENCY_BACKUP_PAGE_HTML)
    # Outer "panic recovery one-liner" block carries `language-bash`.
    assert "```bash\n" in md
    assert "duply --socket" in md
    assert "```\n" in md
    # No stray 4-space indented block anywhere; the source HTML
    # had three more ``language-sh`` snippets inside details.
    assert "\n    duply zoldyck restore" not in md


def test_default_strips_details_wrapper() -> None:
    """When `convert_details` is not set, html2text's legacy
    behaviour wins: ``<details>`` is dropped and the summary
    text is followed by the body text directly underneath."""
    md = _converter().html_to_markdown(EMERGENCY_BACKUP_PAGE_HTML)
    # No <details> tag survives.
    assert "<details>" not in md
    assert "<summary>" not in md
    # The summaries are still emitted (just not wrapped).
    assert "GPG key rotated on yorknew" in md
    assert "rclone cannot reach the dark-continent bucket" in md
    assert "Disk is full on whale-island" in md


# ---- convert_details=True ---------------------------------------------


def test_convert_details_preserves_details_wrapper() -> None:
    md = _converter(convert_details=True).html_to_markdown(
        EMERGENCY_BACKUP_PAGE_HTML,
        options=ConvertOptions(convert_details=True),
    )
    # All three details blocks survive, with the summary text
    # wrapped in <summary> and the body sitting between the
    # opening / closing tags.
    assert md.count("<details>") == 3
    assert md.count("</details>") == 3
    assert md.count("<summary>") == 3
    assert "GPG key rotated on yorknew" in md
    assert "rclone cannot reach the dark-continent bucket" in md
    assert "Disk is full on whale-island" in md
    # The body of each section sits inside its <details> block;
    # pin one snippet so a regression that drops the body back
    # outside the wrapper is caught immediately.
    details_start = md.find("<details>")
    details_end = md.find("</details>") + len("</details>")
    gpg_section = md[details_start:details_end]
    assert "`zoldyck` profile points at the old public key" in gpg_section
    # And the body code block is still rendered as a fenced block
    # inside the wrapper, not as a placeholder.
    assert "```sh\n" in gpg_section
    assert "gpg --gen-key" in gpg_section


def test_convert_details_via_options_overrides_constructor() -> None:
    """`options={"convert_details": True}` on the call site wins
    over the constructor default, so a caller can leave the
    converter at its safe default and still opt in for one
    page without rebuilding the converter."""
    # Converter default is False; the per-call option flips it on.
    md = _converter(convert_details=False).html_to_markdown(
        EMERGENCY_BACKUP_PAGE_HTML,
        options=ConvertOptions(convert_details=True),
    )
    assert "<details>" in md


def test_convert_details_false_via_options_disables_default() -> None:
    """Conversely, a converter built with `convert_details=True`
    can still be called with `convert_details=False` for one
    page to opt out of the new behaviour."""
    md = _converter(convert_details=True).html_to_markdown(
        EMERGENCY_BACKUP_PAGE_HTML,
        options=ConvertOptions(convert_details=False),
    )
    assert "<details>" not in md


# ---- end-to-end orchestrator wiring ------------------------------------


# Local stubs (kept narrow: just enough for the orchestrator
# smoke test below; the full stub suite lives in
# `test_thirdparty_migrations_bookstack.py`).


class _StubNoteService(NoteServiceABC):
    def __init__(self) -> None:
        self.inserted: List[NoteEntity] = []
        self._next_id = 0

    async def insert_note(self, note: NoteEntity, user_ctx: UserContextABC) -> NoteEntity:
        self._next_id += 1
        inserted = NoteEntity(
            note_id=f"note-{self._next_id}",
            title=note.title,
            content=note.content,
            author_id=note.author_id,
            directory_ids=list(note.directory_ids or []),
            permissions=[],
        )
        self.inserted.append(inserted)
        return inserted

    async def update_note(self, note: NoteEntity, user_ctx: UserContextABC) -> NoteEntity:
        return note

    async def get_note(self, note_id, user_ctx, *, include=None):
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):
        raise NotImplementedError

    async def search_notes(self, search_type, query, user_ctx, limit, offset):
        raise NotImplementedError

    async def get_notes(self, note_ids, user_ctx, options=None):
        raise NotImplementedError


class _StubAttachmentFacade(AttachmentFacadeABC):
    def __init__(self) -> None:
        self.posted: List[Attachment] = []

    async def post_attachment(self, attachment: Attachment, user_ctx: UserContextABC) -> Attachment:
        self.posted.append(attachment)
        return Attachment(
            key=f"att-{attachment.filename}",
            filename=attachment.filename,
            filepath=attachment.filepath,
            content_type=attachment.content_type,
            size=attachment.size,
        )

    async def update_metadata(self, attachment, user_ctx):
        raise NotImplementedError

    async def get_attachment(self, key, user_ctx):
        raise NotImplementedError

    async def get_metadata(self, key, user_ctx):
        raise NotImplementedError

    async def delete_attachment(self, key, user_ctx):
        raise NotImplementedError

    async def link_attachment(self, attachment_key, sub_type, sub_id, user_ctx):
        return None

    async def unlink_attachment(self, attachment_key, sub_type, sub_id, user_ctx):
        raise NotImplementedError

    async def list_attachments(self, sub_type, sub_id, user_ctx):
        raise NotImplementedError


def _build_zip(payload: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.json", json.dumps(payload))
    return buf.getvalue()


def _build_importer(
    *,
    note_service: NoteServiceABC | None = None,
    attachment_facade: AttachmentFacadeABC | None = None,
    convert_options: ConvertOptions | None = None,
) -> Tuple[
    BookstackBookImport, _StubDirectoryService, _StubNoteService, _StubAttachmentFacade
]:
    ds = _StubDirectoryService()
    ns = note_service or _StubNoteService()
    af = attachment_facade or _StubAttachmentFacade()

    # Wire the directory stub to auto-increment ids so the
    # orchestrator's chapter / book branch resolves cleanly.
    async def patched(entity, user_ctx):
        ds.next_directory_id += 1
        new_id = f"dir-{ds.next_directory_id}"
        created = DirectoryEntity(
            id=new_id,
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            parent_directory_ids=list(entity.parent_directory_ids or []),
            readme_note_id=UNDEFINED,
            relations=[],
        )
        ds.directories_by_id[new_id] = created
        return created

    ds.create_directory = patched  # type: ignore[attr-defined]
    importer = BookstackBookImport(
        attachment_facade=af,
        directory_service=ds,
        note_service=ns,
        log=silent_logger,
        convert_options=convert_options,
    )
    return importer, ds, ns, af


@pytest.mark.asyncio
async def test_full_orchestrator_emits_fenced_code_block_by_default() -> None:
    """End-to-end pin: even without `convert_details`, every code
    block in the anonymised page lands as a fenced ```` ```X ````
    block in the inserted note, so the renderer cannot regress
    back to the empty-fence behaviour the user reported."""
    importer, _ds, ns, _af = _build_importer()
    await importer.migrate(
        _build_zip(EMERGENCY_BACKUP_BOOK_PAYLOAD),
        _UserContext(user_id="u1"),
    )
    # Find the page we care about (the other stub note is the
    # trivial "Quick reference" direct-child page).
    inserted = next(n for n in ns.inserted if n.title.startswith("Emergency List"))
    content = inserted.content or ""
    # Outer bash block.
    assert "```bash\n" in content
    assert "duply --socket /var/run/duplicity.sock" in content
    # Each of the three inner language-sh blocks survives too.
    assert content.count("```sh\n") >= 3
    # No 4-space indented code block slipped through.
    assert "\n    duply zoldyck restore" not in content


@pytest.mark.asyncio
async def test_full_orchestrator_with_convert_details_preserves_details() -> None:
    """When the importer is constructed with
    ``convert_options={"convert_details": True}``, every details
    block in the page is preserved end-to-end."""
    importer, _ds, ns, _af = _build_importer(
        convert_options=ConvertOptions(convert_details=True),
    )
    await importer.migrate(
        _build_zip(EMERGENCY_BACKUP_BOOK_PAYLOAD),
        _UserContext(user_id="u1"),
    )
    inserted = next(n for n in ns.inserted if n.title.startswith("Emergency List"))
    content = inserted.content or ""
    assert content.count("<details>") == 3
    assert content.count("</details>") == 3
    # The body of the GPG section sits inside the wrapper, with
    # the fenced code block nested under it.
    details_start = content.find("<details>")
    details_end = content.find("</details>") + len("</details>")
    gpg_section = content[details_start:details_end]
    assert "`zoldyck` profile points at the old public key" in gpg_section
    assert "```sh\n" in gpg_section
    assert "gpg --gen-key" in gpg_section


# ---- direct converter pinning of the original regressions -------------


def test_pinned_regression_code_block_has_visible_content() -> None:
    """Regression: the original "Vueko Backup" page rendered every
    code block as ``` ``` because html2text emitted 4-space
    indented blocks the project viewer did not recognise.  The
    fix is to emit fenced code blocks instead."""
    html = (
        '<p>Run this:</p>'
        '<pre><code class="language-bash">echo run-the-chain</code></pre>'
    )
    md = _converter().html_to_markdown(html)
    # Fenced, not indented.
    assert "```bash\necho run-the-chain\n```" in md
    # Sanity: no 4-space indented version left behind.
    assert "\n    echo run-the-chain" not in md


def test_pinned_regression_details_body_sits_inside_wrapper() -> None:
    """Regression: the original page rendered the body of each
    `<details>` directly below the summary, outside any wrapper.
    With `convert_details=True` the body sits inside the
    `<details>` block so the markdown viewer can collapse it."""
    html = (
        '<details><summary>summary text</summary>'
        '<p>body text</p>'
        '</details>'
    )
    md = _converter(convert_details=True).html_to_markdown(
        html, options=ConvertOptions(convert_details=True)
    )
    assert "<details>" in md
    assert "<summary>summary text</summary>" in md
    assert "body text" in md
    # The body text appears between the opening <details> and the
    # closing </details>, not before or after.
    open_at = md.index("<details>")
    close_at = md.index("</details>") + len("</details>")
    assert open_at < md.index("body text") < close_at


def test_pinned_regression_details_with_nested_code_block() -> None:
    """A `<details>` that itself contains a `<pre><code>` block
    must render the inner code block as a fenced block inside
    the collapsible wrapper -- not as a placeholder and not as
    a 4-space indented block outside the wrapper."""
    html = (
        '<details><summary>how to debug</summary>'
        '<p>Run the diagnostic:</p>'
        '<pre><code class="language-sh">echo debug-step-1</code></pre>'
        '</details>'
    )
    md = _converter(convert_details=True).html_to_markdown(
        html, options=ConvertOptions(convert_details=True)
    )
    assert "<details>" in md
    assert "```sh\necho debug-step-1\n```" in md
    # The fenced block is inside the wrapper, not outside it.
    details_open = md.index("<details>")
    details_close = md.index("</details>") + len("</details>")
    fenced_open = md.index("```sh")
    assert details_open < fenced_open < details_close