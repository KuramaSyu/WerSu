"""Backward-compatible re-export of the BookStack HTML converter.

The implementation used to live in this module.  It has been
split into a small package under :mod:`.html_converter` so each
strategy (code-block fencing, details preservation, math-block
rewriting, image-src rewrites, cross-ref rewrites) lives in its
own file.  This module now re-exports the public surface so
``from src.services.thirdparty_migrations.bookstack_html_converter
import BookstackHtmlConverter`` (and the ``ConvertOptions`` /
``AttachmentUrlBuilder`` aliases) keep working for every existing
importer / test / caller.
"""

from __future__ import annotations

from .html_converter import (
    AttachmentUrlBuilder,
    BookstackHtmlConverter,
    CodeBlockStrategy,
    ConvertOptions,
    ConvertPipeline,
    ConverterContext,
    CrossRefStrategy,
    DetailsStrategy,
    HtmlPreprocessorStrategy,
    ImageRefStrategy,
    MarkdownPostprocessorStrategy,
    MathStrategy,
)


__all__ = [
    "AttachmentUrlBuilder",
    "BookstackHtmlConverter",
    "CodeBlockStrategy",
    "ConvertOptions",
    "ConvertPipeline",
    "ConverterContext",
    "CrossRefStrategy",
    "DetailsStrategy",
    "HtmlPreprocessorStrategy",
    "ImageRefStrategy",
    "MarkdownPostprocessorStrategy",
    "MathStrategy",
]