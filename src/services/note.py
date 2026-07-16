"""Back-compat shim.

The implementation moved to :mod:`src.services.note_service`; this
module re-exports its public symbol so existing
``from src.services.note import NoteServiceImpl`` imports keep
working.
"""

from src.services.note_service import NoteServiceImpl

__all__ = ["NoteServiceImpl"]
