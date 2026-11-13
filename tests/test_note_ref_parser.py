"""Unit tests for note reference parsing, extraction and orchestration."""

from __future__ import annotations

from src.api.other.note_ref import NoteRef
from src.services.note_ref_parser import NoteRefParserImpl
from src.db.entities.note.metadata import NoteEntity
from tests.doubles import _FakeNoteContentRepo


def _note(note_id: str, content: str) -> NoteEntity:
    return NoteEntity(
        note_id=note_id,
        title=note_id,
        content=content,
        author_id="user-1",
        permissions=[],
        embeddings=[],
    )


def test_note_ref_from_string_parses_note_and_section() -> None:
    assert NoteRef.from_string("{{@note-1}}") == NoteRef("note-1")
    assert NoteRef.from_string("{{@note-1#details}}") == NoteRef(
        "note-1", "details"
    )
    assert NoteRef.from_string("@note-1#details") == NoteRef("note-1", "details")
    assert str(NoteRef("note-1", "details")) == "{{@note-1#details}}"


def test_parse_refs_finds_distinct_references_in_order() -> None:
    parser = NoteRefParserImpl(_FakeNoteContentRepo())
    refs = parser._parse_refs(
        "{{@note-1}} then {{@note-2#details}} and {{@note-1}} again"
    )
    assert refs == [
        NoteRef("note-1"),
        NoteRef("note-2", "details"),
    ]


async def test_fetch_ref_returns_whole_note_or_only_the_section() -> None:
    repo = _FakeNoteContentRepo()
    repo.seed(
        _note(
            "note-1",
            "# Intro\nopening\n\n## Details\nonly this\n## Other\nnope",
        )
    )
    parser = NoteRefParserImpl(repo)

    assert await parser.fetch_ref(NoteRef("note-1")) == repo._store[
        "note-1"
    ].content
    assert (
        await parser.fetch_ref(NoteRef("note-1", "details"))
        == "## Details\nonly this"
    )
    assert await parser.fetch_ref(NoteRef("note-1", "missing")) == ""


async def test_parse_resolves_every_reference() -> None:
    repo = _FakeNoteContentRepo()
    repo.seed(_note("note-1", "{{@note-2}} {{@note-3#part}}"))
    repo.seed(_note("note-2", "whole"))
    repo.seed(_note("note-3", "# Part\npiece"))

    parsed = await NoteRefParserImpl(repo).parse(repo._store["note-1"])

    assert parsed == {
        NoteRef("note-2"): "whole",
        NoteRef("note-3", "part"): "# Part\npiece",
    }