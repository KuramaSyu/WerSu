"""HTML -> Markdown conversion pipeline for the BookStack importer.

The package is built around the Strategy pattern: each feature
(code-block fencing, details-block preservation, math-block
rewriting, image-src rewrites, BookStack cross-ref rewrites) lives
in its own strategy class under :mod:`.strategies`, and
:class:`ConvertPipeline` glues them together.  The thin
:class:`BookstackHtmlConverter` orchestrator just builds the
default pipeline and runs it.

Public surface (re-exported at the package level):

* :class:`ConvertOptions` -- per-conversion knobs.
* :class:`ConverterContext` -- read-only bag of URL builders /
  bodywidth / :mod:`html2text` instance handed to every strategy.
* :class:`HtmlPreprocessorStrategy` /
  :class:`MarkdownPostprocessorStrategy` -- the two strategy
  protocols.  Implement either one (or both) to register a new
  feature.
* :class:`ConvertPipeline` -- the actual ordered pipeline.
* :class:`BookstackHtmlConverter` -- the entry point the
  orchestrator uses.
* The concrete strategies (:class:`CodeBlockStrategy`,
  :class:`DetailsStrategy`, :class:`MathStrategy`,
  :class:`ImageRefStrategy`, :class:`CrossRefStrategy`).
"""

from .context import ConverterContext
from .converter import BookstackHtmlConverter
from .options import AttachmentUrlBuilder, ConvertOptions
from .pipeline import ConvertPipeline
from .strategy import (
    HtmlPreprocessorStrategy,
    MarkdownPostprocessorStrategy,
)
from .strategies.code_blocks import CodeBlockStrategy
from .strategies.cross_refs import CrossRefStrategy
from .strategies.details import DetailsStrategy
from .strategies.image_refs import ImageRefStrategy
from .strategies.math import MathStrategy


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