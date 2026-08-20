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


# ---- fakes ----------------------------------------------------------------


class _E:
    def __init__(self, title=None, content=None):
        self.title = title
        self.content = content


class FakeNoteContentRepo:
    def __init__(self, mapping: dict[str, _E] | None = None) -> None:
        self._mapping = mapping or {}
        self.raise_on: set[str] = set()

    async def select_by_id(self, note_id: str):
        if note_id in self.raise_on:
            raise RuntimeError("boom")
        return self._mapping.get(note_id, _E())


class FakeDirectoryRepo:
    def __init__(self, parents: dict[str, list[str]] | None = None) -> None:
        # ``parents[child_id] = [parent_id_1, parent_id_2, ...]``
        # (only the first is followed in the chain walk; the
        # rest are ignored, mirroring the production behaviour).
        self._parents = parents or {}
        self.raise_on: set[str] = set()

    async def get_parent_of(self, type, child_id: str):
        if child_id in self.raise_on:
            raise RuntimeError("boom")
        return list(self._parents.get(child_id, []))


# ---- note_content / note_title -------------------------------------------


@pytest.mark.asyncio
async def test_note_content_returns_content_when_present():
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo({"n1": _E(content="hello")}),
        directory_repo=FakeDirectoryRepo(),
    )
    assert await ctx.note_content("n1") == "hello"


@pytest.mark.asyncio
async def test_note_content_returns_none_for_unknown_note():
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=FakeDirectoryRepo(),
    )
    assert await ctx.note_content("n1") is None


@pytest.mark.asyncio
async def test_note_content_swallows_exceptions():
    repo = FakeNoteContentRepo()
    repo.raise_on.add("n1")
    ctx = InMemoryEventContext(note_content_repo=repo, directory_repo=FakeDirectoryRepo())
    assert await ctx.note_content("n1") is None


@pytest.mark.asyncio
async def test_note_title_returns_title_when_present():
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo({"n1": _E(title="T")}),
        directory_repo=FakeDirectoryRepo(),
    )
    assert await ctx.note_title("n1") == "T"


@pytest.mark.asyncio
async def test_note_title_returns_none_for_unknown_note():
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=FakeDirectoryRepo(),
    )
    assert await ctx.note_title("n1") is None


@pytest.mark.asyncio
async def test_note_title_swallows_exceptions():
    repo = FakeNoteContentRepo()
    repo.raise_on.add("n1")
    ctx = InMemoryEventContext(note_content_repo=repo, directory_repo=FakeDirectoryRepo())
    assert await ctx.note_title("n1") is None


# ---- directory_ancestor_ids ---------------------------------------------


@pytest.mark.asyncio
async def test_directory_ancestor_ids_walks_chain():
    # d1's parent is d2, d2's parent is d3, d3 has no parents.
    repo = FakeDirectoryRepo({"d1": ["d2"], "d2": ["d3"], "d3": []})
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=repo,  # type: ignore[arg-type]
    )
    out = await ctx.directory_ancestor_ids("d1")
    assert out == ["d2", "d3"]


@pytest.mark.asyncio
async def test_directory_ancestor_ids_returns_empty_for_root():
    repo = FakeDirectoryRepo()
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=repo,  # type: ignore[arg-type]
    )
    assert await ctx.directory_ancestor_ids("d1") == []


@pytest.mark.asyncio
async def test_directory_ancestor_ids_handles_cycle():
    # d1's parent is d2, d2's parent is d1 -- a cycle.  The walk
    # must terminate; we expect the chain to include d2 and then
    # the second d1 visit, because ``seen`` is only updated as we
    # move to a new parent (we never add the input id to ``seen``).
    repo = FakeDirectoryRepo({"d1": ["d2"], "d2": ["d1"]})
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=repo,  # type: ignore[arg-type]
    )
    out = await ctx.directory_ancestor_ids("d1")
    # The walker visits d2 (chain entry), then walks to d1 (its
    # parent), then on the next iteration d2 is already in ``seen``
    # and the walk breaks.  Two entries.
    assert out == ["d2", "d1"]


@pytest.mark.asyncio
async def test_directory_ancestor_ids_swallows_exceptions():
    repo = FakeDirectoryRepo()
    repo.raise_on.add("d1")
    ctx = InMemoryEventContext(
        note_content_repo=FakeNoteContentRepo(),
        directory_repo=repo,  # type: ignore[arg-type]
    )
    assert await ctx.directory_ancestor_ids("d1") == []
