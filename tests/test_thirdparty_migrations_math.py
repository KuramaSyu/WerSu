r"""Tests pinning the BookStack math conversion.

Two regressions live here, both caught against the real
Faktorisierungsmethoden page from the BA Studium book:

1. **Regex coverage.**  The page's `markdown` field carries
   ``\\[…\\]`` -- the html2text-escaped form of the BookStack
   KaTeX delimiters.  The previous regex required the
   unescaped ``\[…\]` form (a single backslash on each side)
   so it matched only *half* of each delimiter and the result
   was a stray backslash on either side of the rendered math.
   Both the unescaped and the escaped forms now match.

2. **Markdown-field pass.**  Before this fix,
   :meth:`BookstackHtmlConverter.convert_content` only ran
   the math post-processor when the page's `html` field was
   non-empty (the html2text path picks the math strategy up
   via :meth:`ConvertPipeline.run`).  BookStack pages that
   carry a populated `markdown` field skipped the math pass
   entirely, so the user's case left ``\\[…\\]`` text in the
   persisted note.  The converter now runs the math
   post-processor against the `markdown` field directly when
   that is the source the importer ends up persisting.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from src.api.services.directory_service import DirectoryServiceABC
from src.api.services.note_service import NoteServiceABC
from src.api.other.user_context import UserContextABC
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.attachments.attachments import Attachment
from src.services.attachment_facade import AttachmentFacadeABC
from src.services.thirdparty_migrations.bookstack import BookstackBookImport
from src.services.thirdparty_migrations.bookstack_html_converter import (
    BookstackHtmlConverter,
    ConvertOptions,
)
from src.services.thirdparty_migrations.bookstack_models import BookstackPage
from tests._fixtures_pkg.bookstack_emergency_backup import EMERGENCY_BACKUP_BOOK_PAYLOAD
from tests.stubs.directory_service import _StubDirectoryService
from tests.stubs.logging import silent_logger
from tests.stubs.user_context import _UserContext


def _builder(key: str) -> str:
    return f"/u/{key}"


def _converter() -> BookstackHtmlConverter:
    return BookstackHtmlConverter(attachment_url_builder=_builder)


# ---- regex coverage ---------------------------------------------------


def test_block_math_unescaped_form_is_converted() -> None:
    r"""``\[...\\]`` (one backslash on each side) -- the form the
    BookStack editor saves -- becomes ``$$...$$``."""
    md = _converter().html_to_markdown(
        "<p>\\[" + "<br/>" + "6x^2 + 9x" + "<br/>" + "\\]</p>"
    )
    assert "$$6x^2 + 9x$$" in md
    assert "\\[" not in md
    assert "\\]" not in md


def test_block_math_escaped_form_is_converted() -> None:
    r"""``\\[…\\]`` (two backslashes on each side) -- the form
    html2text emits for the editor's ``\[…\\]`` -- becomes
    ``$$…$$``.  This is the regression the user reported:
    the previous regex matched only ``\[` (one backslash)
    so half the delimiter survived in the rendered note.
    """
    # Source HTML carries the editor's literal two-character
    # ``\[`` form; html2text escapes each backslash, so the
    # markdown we see carries ``\\[`` (three characters:
    # backslash, backslash, bracket).
    md = _converter().html_to_markdown(
        "<p>\\[" + "<br/>" + "6x^2 + 9x" + "<br/>" + "\\]</p>"
    )
    assert "$$6x^2 + 9x$$" in md
    assert "\\[" not in md
    assert "\\]" not in md


def test_inline_math_unescaped_form_is_converted() -> None:
    md = _converter().html_to_markdown("<p>inline \\(x = 1\\) test</p>")
    assert "$x = 1$" in md


def test_inline_math_escaped_form_is_converted() -> None:
    md = _converter().html_to_markdown("<p>inline \\(x = 1\\) test</p>")
    assert "$x = 1$" in md


def test_br_inside_math_block_is_collapsed() -> None:
    """``<br/>`` tags the editor inserts inside multi-line math
    are collapsed to a single space so the resulting ``$$…$$``
    block stays on one logical line and the renderer does not
    pick up stray WYSIWYG line breaks inside the formula."""
    md = _converter().html_to_markdown(
        "<p>\\[" + "<br/>" + "x +<br/>" + "y" + "<br/>" + "\\]</p>"
    )
    # ``<br/>`` inside the body becomes whitespace; the
    # ``$$…$$`` block ends up without literal ``<br/>`` tags.
    assert "$$" in md
    assert "<br/>" not in md
    assert "<br>" not in md
    assert "x +" in md
    assert "y" in md


def test_math_can_be_disabled_via_options() -> None:
    body = "<p>\\[" + "x" + "\\]</p>"
    md_off = _converter().html_to_markdown(
        body, options=ConvertOptions(convert_math=False)
    )
    # Both escaped delimiters survive -- the strategy is
    # off so the user's literal text passes through.
    assert "\\[" in md_off
    assert "\\]" in md_off
    assert "$$" not in md_off


# ---- convert_content math pass on the page's markdown field ---------


def test_convert_content_runs_math_strategy_on_markdown_field() -> None:
    """When the page's `markdown` field is non-empty, the
    converter runs the math strategy on it directly.  Previously
    the math pass only ran on the html2text output, so a page
    with a populated `markdown` field had its ``\\[ ... \\]``
    delimiters left untouched in the persisted note."""
    page = BookstackPage(
        id=1,
        name="P",
        # html2text-escaped form the BookStack editor saves
        # directly when the user typed it in the markdown tab.
        markdown="intro\n\n" + "\\\\[" + "x^2 + 1" + "\\\\]" + "\n\noutro",
        html="",
    )
    out = _converter().convert_content(page, {})
    assert "$$x^2 + 1$$" in out
    assert "\\\\[" not in out


def test_convert_content_math_disabled_via_options() -> None:
    """``convert_math=False`` on the converter constructor
    skips the math pass on the markdown field too."""
    page = BookstackPage(
        id=1, name="P",
        markdown="\\\\[" + "x^2" + "\\\\]",
        html="",
    )
    conv = BookstackHtmlConverter(
        attachment_url_builder=_builder,
        convert_math=False,
    )
    out = conv.convert_content(page, {})
    assert "\\\\[" in out
    assert "$$" not in out


# ---- end-to-end against the real Faktorisierungsmethoden page -----


def _build_zip(payload: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.json", json.dumps(payload))
    return buf.getvalue()


class _StubNoteService(NoteServiceABC):
    def __init__(self) -> None:
        self.inserted: list[NoteEntity] = []
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

    async def update_note(self, note, user_ctx):
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
    async def post_attachment(self, attachment: Attachment, user_ctx: UserContextABC) -> Attachment:
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


def _build_importer(
    *, note_service: NoteServiceABC | None = None,
    attachment_facade: AttachmentFacadeABC | None = None,
) -> tuple[BookstackBookImport, _StubDirectoryService, _StubNoteService, _StubAttachmentFacade]:
    ds = _StubDirectoryService()
    ns = note_service or _StubNoteService()
    af = attachment_facade or _StubAttachmentFacade()

    async def patched(entity, user_ctx):
        ds.next_directory_id += 1
        new_id = f"dir-{ds.next_directory_id}"
        from src.db.entities.directory.directory import DirectoryEntity
        from src.api.other.undefined import UNDEFINED
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
    )
    return importer, ds, ns, af


def _book_with_faktor_page() -> dict:
    """Build a book payload that mirrors the real Faktorisierungs-
    methoden page: a chapter with one page whose `markdown` field
    carries the html2text-escaped math delimiters the BookStack
    editor saves."""
    return {
        "book": {
            "name": "BA Studium",
            "description_html": "",
            "cover": None,
            "chapters": [
                {
                    "id": 47,
                    "name": "Mathe",
                    "description_html": "",
                    "priority": 9,
                    "pages": [
                        {
                            "id": 428,
                            "name": "Faktorisierungsmethoden",
                            "html": "",
                            "markdown": (
                                "## 1. Gemeinsamen Faktor ausklammern\n\n"
                                "**Beispiel:**  \n"
                                "\\\\[\n"
                                "6x^2 + 9x = 3x(2x + 3)\n"
                                "\\\\]\n\n"
                                "Inline \\(a \\cdot c\\) text."
                            ),
                            "priority": 1,
                            "attachments": [],
                            "images": [],
                            "tags": [],
                        }
                    ],
                }
            ],
            "pages": [],
        }
    }


@pytest.mark.asyncio
async def test_faktorisierungsmethoden_math_is_converted_end_to_end() -> None:
    """End-to-end pin for the user-reported regression: import a
    page with the BookStack-escaped math delimiters and assert
    that every block is rewritten to ``$$ ... $$`` (and every
    inline to ``$ ... $``) in the persisted note."""
    importer, _ds, ns, _af = _build_importer()
    await importer.migrate(
        _build_zip(_book_with_faktor_page()),
        _UserContext(user_id="u1"),
    )
    inserted = next(n for n in ns.inserted if n.title == "Faktorisierungsmethoden")
    content = inserted.content or ""

    # The block delimiter in the user's markdown was
    # ``\\[ ... \\]`` (two backslashes + bracket each side); the
    # rendered note must contain ``$$6x^2 + 9x = 3x(2x + 3)$$``.
    assert "$$6x^2 + 9x = 3x(2x + 3)$$" in content
    # And no ``\\`` delimiter survives.
    assert "\\\\[" not in content
    assert "\\\\]" not in content

    # The inline delimiter was ``\\( ... \\)``.
    assert "$a \\cdot c$" in content
    assert "\\\\(" not in content
    assert "\\\\)" not in content


def test_default_pipeline_still_emits_emergency_backup_correctly() -> None:
    """The existing emergency-backup fixture (no math) still
    renders cleanly through the default pipeline -- sanity
    check that the math regex changes did not break
    non-math pages."""
    from tests._fixtures_pkg.bookstack_emergency_backup import (
        EMERGENCY_BACKUP_BOOK_PAYLOAD,
    )
    html = EMERGENCY_BACKUP_BOOK_PAYLOAD["book"]["chapters"][0]["pages"][0]["html"]
    # ``convert_details`` defaults to True, so the default
    # converter preserves the ``<details>`` wrappers.
    md = _converter().html_to_markdown(html)
    assert "```bash" in md
    assert md.count("<details>") == 3

    # ``convert_details=False`` falls back to the legacy
    # html2text behaviour: wrapper tags are stripped.
    md_no_details = BookstackHtmlConverter(
        attachment_url_builder=_builder, convert_details=False,
    ).html_to_markdown(html)
    assert "<details>" not in md_no_details