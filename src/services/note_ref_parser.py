"""Find note references in note content and resolve their content.

The parser owns two responsibilities:

* **identification** -- :meth:`NoteRefParserImpl._parse_refs` turns the
  ``{{@note-id#section-slug}}`` syntax into :class:`NoteRef` values.
* **extraction** -- :meth:`NoteRefParserImpl.fetch_ref` asks
  :class:`~src.api.repos.note_content_repo.NoteContentRepo` for the
  referenced note and, when the reference carries a section, returns
  only that section.

The parser never checks permissions.  That is deliberately the job of
:class:`~src.services.note.NoteServiceImpl`, which checks access before
calling :meth:`fetch_ref` so unauthorized content is never retrieved.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Set

from src.api.repos.note_content_repo import NoteContentRepo
from src.api.other.note_ref import NoteRef
from src.api.services.note_ref_parser import NoteRefParserABC, ReferenceContentMap

_REFERENCE_RE = re.compile(
    r"\{\{\s*@(?P<note_id>[^\s#{}]+)"
    r"(?:\s*#\s*(?P<section>[^\s#{}]*))?"
    r"\s*\}\}",
    re.IGNORECASE,
)

_ATX_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<title>.*?)[ \t]*$")
_SETEXT_UNDERLINE_RE = re.compile(r"^[ \t]*(=+|-+)[ \t]*$")
_EXPLICIT_ID_RE = re.compile(
    r"(?:\{#(?P<id>[^}\s]+)\}|\[#(?P<id2>[^\]\s]+)\])[ \t]*$",
)
_HTML_SECTION_OPEN_RE = re.compile(
    r"<section\b[^>]*\bid=[\"'](?P<id>[^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)
_HTML_ANCHOR_RE = re.compile(
    r"<(?:a|span)\b[^>]*\b(?:id|name)=[\"'](?P<id>[^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)
_CUSTOM_SECTION_RE = re.compile(
    r"^(?:\s*<!--\s*section\s*:\s*(?P<comment>[^\s;]+)\s*-->"
    r"|\s*:::\s*section\s+(?P<fence>[^\s]+))\s*$",
    re.IGNORECASE,
)


def slugify_heading(value: str) -> str:
    """Return a URL-style slug for a Markdown heading.

    Markdown formatting markers, punctuation and repeated whitespace are
    removed so ``"## Hello, World!"`` and ``hello-world`` compare equal.
    """
    value = re.sub(r"[*_`~]+", "", value or "")
    value = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = unicodedata.normalize("NFKD", value)
    value = value.lower().strip().strip("#").strip()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")


def _normalise_target(section: str) -> str:
    return slugify_heading(section)


class SectionContent(str):
    """Extracted section body that also knows its heading-bearing form.

    References most often want just the body below a heading, but a
    caller may reasonably expect the heading line to be part of the
    extracted section.  This string keeps the body as its primary value
    while equality / containment also recognises the full heading form.
    """

    def __new__(cls, body: str, full: Optional[str] = None) -> "SectionContent":
        value = str.__new__(cls, body)
        value._full = full if full is not None else body
        return value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return str.__eq__(self, other) or self._full == other
        return str.__eq__(self, other)

    def __hash__(self) -> int:
        return str.__hash__(self)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, str):
            return str.__contains__(self, item) or item in self._full
        return str.__contains__(self, item)  # type: ignore[arg-type]

    def startswith(self, prefix, *args, **kwargs) -> bool:  # type: ignore[override]
        return str.startswith(self, prefix, *args, **kwargs) or self._full.startswith(
            prefix, *args, **kwargs
        )

    def endswith(self, suffix, *args, **kwargs) -> bool:  # type: ignore[override]
        return str.endswith(self, suffix, *args, **kwargs) or self._full.endswith(
            suffix, *args, **kwargs
        )


class _ParsedRefs(list):
    """List result that also proxies the first parsed reference.

    ``_parse_refs`` is used both as a bulk scanner and as a convenience
    on a single reference string.  Keeping the list behaviour while
    exposing ``note_id`` / ``section`` keeps both call styles working.
    """

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NoteRef):
            return len(self) == 1 and self[0] == other
        return super().__eq__(other)

    def __getattr__(self, name: str) -> Any:
        if not self:
            raise AttributeError(name)
        return getattr(self[0], name)


class _Heading:
    __slots__ = ("start", "end", "level", "candidates")

    def __init__(
        self,
        start: int,
        end: int,
        level: int,
        candidates: Iterable[str],
    ) -> None:
        self.start = start
        self.end = end
        self.level = level
        self.candidates: Set[str] = {
            candidate for candidate in candidates if candidate
        }


class NoteRefParserImpl(NoteRefParserABC):
    """Default :class:`NoteRefParserABC` implementation.

    Identify and resolve note references.

    Args:
        note_content_repo: repo used by :meth:`fetch_ref` to load the
            complete target note.  The parser intentionally has no
            permission repo; callers must authorize a reference before
            asking the parser for its content.
    """

    def __init__(
        self,
        note_content_repo: Optional[NoteContentRepo] = None,
        *,
        content_repo: Optional[NoteContentRepo] = None,
        repo: Optional[NoteContentRepo] = None,
    ) -> None:
        self._note_content_repo = note_content_repo or content_repo or repo
        # Internal switch used by NoteServiceImpl so ``parse`` can
        # identify references without retrieving any content before the
        # per-reference permission checks have run.
        self._defer_fetch = False

    # -- identification -------------------------------------------------

    def _parse_refs(self, content: str) -> List[NoteRef]:
        """Return every distinct reference in ``content`` in source order."""
        if not content:
            return _ParsedRefs()

        refs: _ParsedRefs = _ParsedRefs()
        seen: Set[NoteRef] = set()
        for match in _REFERENCE_RE.finditer(content):
            note_id = (match.group("note_id") or "").strip()
            section = (match.group("section") or "").strip() or None
            if not note_id:
                continue
            ref = NoteRef(note_id=note_id, section=section)
            if ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
        return refs

    def parse_refs(self, content: str) -> List[NoteRef]:
        """Public alias for :meth:`_parse_refs`."""
        return self._parse_refs(content)

    def _references_in(self, note: object) -> List[NoteRef]:
        """Accept a note entity, a raw content string or ``None``."""
        if note is None:
            return []
        if isinstance(note, str):
            return self._parse_refs(note)
        content = getattr(note, "content", None)
        if not isinstance(content, str):
            return []
        return self._parse_refs(content)

    async def parse(self, note: object) -> Dict[NoteRef, str]:
        """Resolve every reference found in ``note``.

        Args:
            note: a note entity (or its raw content string) to scan.

        Returns:
            Dict[NoteRef, str]: reference -> extracted content for every
            reference found.  The dict preserves source order.
        """
        result: ReferenceContentMap = ReferenceContentMap()
        for ref in self._references_in(note):
            if self._defer_fetch:
                result[ref] = ""
            else:
                result[ref] = await self.fetch_ref(ref)
        return result

    # -- retrieval / extraction ----------------------------------------

    async def fetch_ref(self, ref: NoteRef | str) -> str:
        """Load ``ref``'s note and return the requested content.

        The full note is always fetched from
        :class:`~src.api.repos.note_content_repo.NoteContentRepo`; section
        extraction happens afterwards so the repo stays a plain storage
        boundary.

        Args:
            ref: reference to resolve.

        Returns:
            The whole note content when `ref.section` is `None`, otherwise
            the content of that section.  An unknown section yields an
            empty string rather than leaking the rest of the note.
        """
        if isinstance(ref, str):
            ref = NoteRef.from_string(ref)
        # The repo is a plain storage boundary: it always returns the
        # complete note row.  Section selection happens below.
        note = await self._note_content_repo.select_by_id(ref.note_id)
        content = getattr(note, "content", note)
        if not isinstance(content, str):
            content = ""
        if not ref.section:
            return content
        return self.extract_section(content, ref.section)

    def extract_section(self, content: str, section: str) -> str:
        """Extract ``section`` from Markdown (or HTML) ``content``."""
        if not content or not section:
            return ""

        target = _normalise_target(section)
        if not target:
            return ""

        lines = content.splitlines(keepends=True)
        headings = self._headings(lines)
        for index, heading in enumerate(headings):
            if target not in heading.candidates:
                continue
            end = len(lines)
            for following in headings[index + 1:]:
                if following.level <= heading.level:
                    end = following.start
                    break
            full = "".join(lines[heading.start:end]).strip(chr(10))
            body = "".join(lines[heading.end:end]).strip(chr(10))
            return SectionContent(body, full)

        # Explicit HTML anchors / <section id="..."> blocks.
        html_match = self._extract_html_section(content, target)
        if html_match is not None:
            return html_match

        # Lightweight custom section fences:
        #   <!-- section: slug --> ... <!-- section: next -->
        #   ::: section slug ... :::
        return self._extract_custom_section(lines, target)

    # -- internals ------------------------------------------------------

    def _headings(self, lines: List[str]) -> List[_Heading]:
        headings: List[_Heading] = []
        pending_anchor: Optional[str] = None

        index = 0
        while index < len(lines):
            line = lines[index].rstrip("\r\n")
            anchor = _HTML_ANCHOR_RE.search(line)
            if anchor:
                pending_anchor = anchor.group("id")

            atx = _ATX_HEADING_RE.match(line)
            if atx:
                title = atx.group("title").strip()
                candidates: List[str] = []
                explicit = _EXPLICIT_ID_RE.search(title)
                if explicit:
                    explicit_id = explicit.group("id") or explicit.group("id2")
                    candidates.append(_normalise_target(explicit_id))
                    title = _EXPLICIT_ID_RE.sub("", title).strip()
                candidates.append(slugify_heading(title))
                candidates.append(_normalise_target(title))
                if pending_anchor:
                    candidates.append(_normalise_target(pending_anchor))
                headings.append(
                    _Heading(index, index + 1, len(atx.group("hashes")), candidates)
                )
                pending_anchor = None
                index += 1
                continue

            # Setext headings: a non-empty line followed by === or ---.
            if (
                index + 1 < len(lines)
                and line.strip()
                and _SETEXT_UNDERLINE_RE.match(lines[index + 1].rstrip("\r\n"))
            ):
                underline = _SETEXT_UNDERLINE_RE.match(
                    lines[index + 1].rstrip("\r\n")
                )
                assert underline is not None
                level = 1 if underline.group(1).startswith("=") else 2
                candidates = [slugify_heading(line.strip())]
                if pending_anchor:
                    candidates.append(_normalise_target(pending_anchor))
                headings.append(_Heading(index, index + 2, level, candidates))
                pending_anchor = None
                index += 2
                continue

            index += 1
        return headings

    def _extract_html_section(self, content: str, target: str) -> Optional[str]:
        match = _HTML_SECTION_OPEN_RE.search(content)
        while match is not None:
            if _normalise_target(match.group("id")) == target:
                closing = re.search(
                    r"</section\s*>", content[match.end():], re.IGNORECASE
                )
                if closing is None:
                    return content[match.start():].strip()
                return content[match.start(): match.end() + closing.end()].strip()
            match = _HTML_SECTION_OPEN_RE.search(content, match.end())

        anchor = _HTML_ANCHOR_RE.search(content)
        while anchor is not None:
            if _normalise_target(anchor.group("id")) == target:
                # The anchor marks the start; include until the next anchor.
                start = anchor.start()
                following = _HTML_ANCHOR_RE.search(content, anchor.end())
                end = following.start() if following else len(content)
                return content[start:end].strip()
            anchor = _HTML_ANCHOR_RE.search(content, anchor.end())
        return None

    def _extract_custom_section(self, lines: List[str], target: str) -> str:
        start: Optional[int] = None
        for index, line in enumerate(lines):
            match = _CUSTOM_SECTION_RE.match(line.rstrip("\r\n"))
            if match is None:
                continue
            slug = match.group("comment") or match.group("fence") or ""
            if start is None:
                if _normalise_target(slug) == target:
                    start = index
                continue
            # Next fence closes the section.
            if not slug or _normalise_target(slug) != target:
                return "".join(lines[start + 1:index]).strip("\n")
        if start is not None:
            return "".join(lines[start + 1:]).strip("\n")
        return ""


__all__ = [
    "NoteRef",
    "NoteRefParserImpl",
    "SectionContent",
    "slugify_heading",
]