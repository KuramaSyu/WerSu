"""Value object representing a reference to another note.

A reference is written as ``{{@note-id}}`` or, when only one section
of the target note is wanted, ``{{@note-id#section-slug}}``.  The
syntax itself lives in :class:`~src.services.note_ref_parser.NoteRefParserImpl`;
this module only carries the parsed representation so every layer can
pass references around without re-parsing strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# The braces are deliberately optional on the outside so callers can also
# pass the inner ``@note#section`` form.  Whitespace inside the braces is
# tolerated because references are authored by humans.
_REFERENCE_RE = re.compile(
    r"\{\{\s*@(?P<note_id>[^\s#{}]+)"
    r"(?:\s*#\s*(?P<section>[^\s#{}]*))?"
    r"\s*\}\}",
    re.IGNORECASE,
)

_BARE_REFERENCE_RE = re.compile(
    r"^@?(?P<note_id>[^\s#{}]+)(?:#(?P<section>[^\s#{}]*))?$",
)


@dataclass(frozen=True)
class NoteRef:
    """A note id plus an optional section slug.

    Attributes:
        note_id: id of the referenced note.
        section: section slug inside the note, or `None` when the whole
            note is referenced.
    """

    note_id: str
    section: Optional[str] = None

    @classmethod
    def from_string(cls, reference: str) -> "NoteRef":
        """Parse ``reference`` into a :class:`NoteRef`.

        Both the full note syntax (``{{@note-id#section-slug}}``) and
        the bare inner form (``@note-id#section-slug``) are accepted.

        Raises:
            ValueError: when `reference` does not contain a usable note id.
        """
        if not isinstance(reference, str):
            raise ValueError(f"Invalid note reference: {reference!r}")

        text = reference.strip()
        match = _REFERENCE_RE.search(text)
        if match is None:
            match = _BARE_REFERENCE_RE.match(text)
        if match is None:
            raise ValueError(f"Invalid note reference: {reference!r}")

        note_id = (match.group("note_id") or "").strip()
        section = (match.group("section") or "").strip() or None
        if not note_id:
            raise ValueError(f"Invalid note reference: {reference!r}")

        return cls(note_id=note_id, section=section)

    def __str__(self) -> str:
        """Render the canonical ``{{@note-id#section}}`` form."""
        suffix = f"#{self.section}" if self.section else ""
        return f"{{{{@{self.note_id}{suffix}}}}}"

    @property
    def is_section(self) -> bool:
        """`True` when this reference points at a section, not a whole note."""
        return bool(self.section)


__all__ = ["NoteRef"]