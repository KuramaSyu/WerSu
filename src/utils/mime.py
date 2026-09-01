"""Tiny wrappers around :mod:`mimetypes` with safe fallbacks."""

from __future__ import annotations

import mimetypes


def guess_content_type(filename: str) -> str:
    """Best-effort content type lookup that falls back to ``application/octet-stream``."""
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


__all__ = ["guess_content_type"]
