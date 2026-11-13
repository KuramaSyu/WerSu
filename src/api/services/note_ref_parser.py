"""Contract for finding and resolving references inside note content.

The service layer depends on :class:`NoteRefParserABC` only.  Swapping
the implementation (different reference syntax, different section
extraction rules, ...) therefore does not require touching
:class:`~src.services.note.NoteServiceImpl`.

Parsers never perform permission checks.  The service authorizes each
reference before calling :meth:`fetch_ref`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List

from src.api.other.note_ref import NoteRef


class NoteRefParserABC(ABC):
    """Identify references and extract content from referenced notes.

    Implementations:
        * :class:`~src.services.note_ref_parser.NoteRefParserImpl` -- the
          default Markdown/HTML implementation.
    """

    @abstractmethod
    async def parse(self, note: object) -> Dict[NoteRef, str]:
        """Resolve every reference found in ``note``.

        Args:
            note: a note entity or raw content string to scan.

        Returns:
            Dict[NoteRef, str]: reference -> extracted content.
        """
        ...

    @abstractmethod
    def _parse_refs(self, content: str) -> List[NoteRef]:
        """Return the distinct references contained in ``content``."""
        ...

    def parse_refs(self, content: str) -> List[NoteRef]:
        """Public identification-only entry point.

        The service uses this to discover references before checking
        permissions; it never retrieves content at this stage.
        """
        return self._parse_refs(content)

    @abstractmethod
    async def fetch_ref(self, ref: NoteRef | str) -> str:
        """Load the referenced note and return the requested content.

        The reference must already have been authorized by the caller.
        """
        ...


class ReferenceContentMap(dict):
    """Mapping of :class:`NoteRef` -> content with list-friendly equality."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return dict.__eq__(self, other)
        if isinstance(other, (list, tuple)):
            values = list(other)
            if all(isinstance(value, NoteRef) for value in values):
                return list(self.keys()) == values
            return list(self.items()) == values
        return NotImplemented


__all__ = [
    "NoteRef",
    "NoteRefParserABC",
    "ReferenceContentMap",
]
