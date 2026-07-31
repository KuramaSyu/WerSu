"""Concrete conversion strategies.

Each module wraps one feature (code-block fencing, details-block
preservation, math-block rewriting, image-src rewrites,
BookStack cross-ref rewrites) as a strategy that implements
:class:`~src.services.thirdparty_migrations.html_converter.strategy.HtmlPreprocessorStrategy`
and / or
:class:`~src.services.thirdparty_migrations.html_converter.strategy.MarkdownPostprocessorStrategy`.

Adding a new strategy
---------------------

1. Pick the right module name under this directory.
2. Implement the strategy class.  Most strategies implement
   only one of the two protocols; pick whichever side the
   feature actually needs.
3. Expose it via :mod:`~src.services.thirdparty_migrations.html_converter.__init__`
   and add it to the default pipeline in
   :class:`~src.services.thirdparty_migrations.html_converter.BookstackHtmlConverter`.
"""

from .code_blocks import CodeBlockStrategy
from .cross_refs import CrossRefStrategy
from .details import DetailsStrategy
from .image_refs import ImageRefStrategy
from .math import MathStrategy


__all__ = [
    "CodeBlockStrategy",
    "CrossRefStrategy",
    "DetailsStrategy",
    "ImageRefStrategy",
    "MathStrategy",
]