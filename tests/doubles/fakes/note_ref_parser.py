from __future__ import annotations

from typing import Dict, List, Optional

from src.api.other.note_ref import NoteRef
from src.api.services.note_ref_parser import NoteRefParserABC, ReferenceContentMap


class _FakeNoteRefParser(NoteRefParserABC):
    """Configurable reference parser for service-level tests."""

    def __init__(
        self,
        refs: Optional[List[NoteRef]] = None,
        contents: Optional[Dict[NoteRef, str]] = None,
    ) -> None:
        self.refs = list(refs or [])
        self.contents = dict(contents or {})
        self.parse_calls: List[object] = []
        self.parse_refs_calls: List[str] = []
        self.fetch_calls: List[NoteRef] = []

    async def parse(self, note: object) -> Dict[NoteRef, str]:
        self.parse_calls.append(note)
        return ReferenceContentMap(
            {ref: self.contents.get(ref, "") for ref in self.refs}
        )

    def _parse_refs(self, content: str) -> List[NoteRef]:
        self.parse_refs_calls.append(content)
        return list(self.refs)

    async def fetch_ref(self, ref: NoteRef | str) -> str:
        if isinstance(ref, str):
            ref = NoteRef.from_string(ref)
        self.fetch_calls.append(ref)
        return self.contents.get(ref, "")


__all__ = ["_FakeNoteRefParser"]
