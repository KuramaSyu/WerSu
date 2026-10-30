"""Tests for :class:`InMemoryEventContext` -- the production
:class:`EventContext` backed by the note + directory repos.

The tests use fake repos that mirror the small surface the
context calls.  We do not need a real Postgres / SpiceDB here
because the context is supposed to be best-effort -- errors from
the repos are swallowed and the method returns ``None`` / ``[]``.
"""

from __future__ import annotations

import pytest

from src.services.event_context import InMemoryEventContext

from tests.doubles.fakes.event_context import FakeNoteContentRepo, FakeDirectoryRepo, FakeShelfRepo, _E


# ---- fakes ----------------------------------------------------------------










# ---- note_content / note_title -------------------------------------------


async def test_note_content_returns_content_when_present():
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo({"n1": _E(content="hello")}),
        directory_repo=FakeDirectoryRepo(), shelf_repo=FakeShelfRepo(),
    )
    assert await ctx.note_content("n1") == "hello"


async def test_note_content_returns_none_for_unknown_note():
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=FakeDirectoryRepo(), shelf_repo=FakeShelfRepo(),
    )
    assert await ctx.note_content("n1") is None


async def test_note_content_swallows_exceptions():
    repo = FakeNoteContentRepo()
    repo.raise_on.add("n1")
    ctx = InMemoryEventContext(note_content_repo=repo, directory_repo=FakeDirectoryRepo(), shelf_repo=FakeShelfRepo())
    assert await ctx.note_content("n1") is None


async def test_note_title_returns_title_when_present():
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo({"n1": _E(title="T")}),
        directory_repo=FakeDirectoryRepo(), shelf_repo=FakeShelfRepo(),
    )
    assert await ctx.note_title("n1") == "T"


async def test_note_title_returns_none_for_unknown_note():
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=FakeDirectoryRepo(), shelf_repo=FakeShelfRepo(),
    )
    assert await ctx.note_title("n1") is None


async def test_note_title_swallows_exceptions():
    repo = FakeNoteContentRepo()
    repo.raise_on.add("n1")
    ctx = InMemoryEventContext(note_content_repo=repo, directory_repo=FakeDirectoryRepo(), shelf_repo=FakeShelfRepo())
    assert await ctx.note_title("n1") is None


# ---- directory_ancestor_ids ---------------------------------------------


async def test_directory_ancestor_ids_walks_chain():
    # d1's parent is d2, d2's parent is d3, d3 has no parents.
    repo = FakeDirectoryRepo({"d1": ["d2"], "d2": ["d3"], "d3": []})
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=repo, shelf_repo=FakeShelfRepo(),  # type: ignore[arg-type]
    )
    out = await ctx.directory_ancestor_ids("d1")
    assert out == ["d2", "d3"]


async def test_directory_ancestor_ids_returns_empty_for_root():
    repo = FakeDirectoryRepo()
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=repo, shelf_repo=FakeShelfRepo(),  # type: ignore[arg-type]
    )
    assert await ctx.directory_ancestor_ids("d1") == []


async def test_directory_ancestor_ids_handles_cycle():
    # d1's parent is d2, d2's parent is d1 -- a cycle.  The walk
    # must terminate; we expect the chain to include d2 and then
    # the second d1 visit, because ``seen`` is only updated as we
    # move to a new parent (we never add the input id to ``seen``).
    repo = FakeDirectoryRepo({"d1": ["d2"], "d2": ["d1"]})
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=repo, shelf_repo=FakeShelfRepo(),  # type: ignore[arg-type]
    )
    out = await ctx.directory_ancestor_ids("d1")
    # The walker visits d2 (chain entry), then walks to d1 (its
    # parent), then on the next iteration d2 is already in ``seen``
    # and the walk breaks.  Two entries.
    assert out == ["d2", "d1"]


async def test_directory_ancestor_ids_swallows_exceptions():
    repo = FakeDirectoryRepo()
    repo.raise_on.add("d1")
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=repo, shelf_repo=FakeShelfRepo(),  # type: ignore[arg-type]
    )
    assert await ctx.directory_ancestor_ids("d1") == []
