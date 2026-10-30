"""Re-export of the canonical undefined-collapsing helpers.

The implementation lives next to the :data:`UNDEFINED` sentinel in
:mod:`src.api.other.undefined`; this module only re-exports it so
``src.utils`` consumers keep a stable import path.
"""

from src.api.other.undefined import resolve_undefined_none

__all__ = ["resolve_undefined_none"]
