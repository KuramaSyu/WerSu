r"""Rewrite BookStack math syntax to the project's standard delimiters.

BookStack's WYSIWYG editor emits KaTeX-style math with two
quirks that the project's markdown viewer does not understand:

1. Block math is written as ``\[ ... \]`` rather than
   ``$$ ... $$`` (the editor saves the literal two-character
   form; :mod:`html2text` then escapes it to the three-character
   ``\\[ ... \\]`` on the way through the converter).
2. Inline math is written as ``\( ... \)`` rather than
   ``$ ... $`` (same escape handling as the block form).
3. The editor often breaks lines with ``<br/>`` *inside* the
   math delimiters (which would otherwise render as stray line
   breaks inside the rendered formula).

This strategy is a markdown post-processor: it runs *after*
:mod:`html2text` so it sees the ``\[`` / ``\]`` / ``\(`` / ``\)``
text already extracted from the HTML, then rewrites every
delimited math region to ``$$ ... $$`` / ``$ ... $`` and drops
the inner ``<br/>`` line breaks along the way.
"""

from __future__ import annotations

import re
from typing import Optional

from ..context import ConverterContext
from ..strategy import MarkdownPostprocessorStrategy


# Match a math block: ``\[ ... \]`` possibly spanning multiple
# lines.  BookStack's WYSIWYG saves the delimiter as the
# literal two-character sequence ``\[``, but :mod:`html2text`
# escapes the backslash on the way out so the markdown we see
# carries the three-character form ``\\[``; some pages also
# reach us with the unescaped form when the editor saved them
# as raw markdown.  We accept either via alternation on each
# side and use ``re.DOTALL`` so the body match spans the
# ``<br/>`` line breaks the editor inserts inside multi-line
# formulas.
_MATH_BLOCK_RE = re.compile(
    r"(?:\\\[|\\\\\[)(?P<body>.*?)(?:\\\]|\\\\\])",
    re.DOTALL,
)

# Match inline math: ``\( ... \)``.  Same escape-handling as
# the block form above: accept either the unescaped ``\(`` /
# ``\)`` or the html2text-escaped ``\\(` / ``\\)`.
_MATH_INLINE_RE = re.compile(
    r"(?:\\\(|\\\\\()(?P<body>.*?)(?:\\\)|\\\\\))",
    re.DOTALL,
)

# ``<br/>`` / ``<br>`` tags inside a math region -- the editor
# emits these so the WYSIWYG renders multi-line formulas.  We
# collapse them to a single space so the resulting ``$$ ... $$``
# block stays on one logical line.
_BR_TAG_RE = re.compile(
    r"<br\s*/?>",
    re.IGNORECASE,
)


class MathStrategy(MarkdownPostprocessorStrategy):
    r"""Rewrite ``\[..\]`` / ``\(..\)`` to ``$$..$$`` / ``$..$``.

    Default behaviour: enabled.  Set
    ``options={"convert_math": False}`` (or pass
    ``convert_math=False`` to the converter constructor) to keep
    the BookStack KaTeX delimiters untouched.

    Implementation notes
    --------------------

    * Block math (``\[ ... \]`` or the html2text-escaped form
      ``\\[ ... \\]``) is rewritten to ``$$ ... $$``.
    * Inline math (``\( ... \)`` or the html2text-escaped form
      ``\\( ... \\)``) is rewritten to ``$ ... $``.
    * ``<br/>`` / ``<br>`` tags *inside* either region are
      collapsed to a single space so the resulting math block
      does not leak WYSIWYG line breaks into the rendered
      formula.
    * The strategy is purely textual -- it does not parse the
      formula body, so escaping inside formulas is the user's
      responsibility.
    """

    def __init__(self, *, default_enabled: bool = True) -> None:
        self._default_enabled = default_enabled

    @property
    def name(self) -> str:
        return "math"

    def postprocess_markdown(
        self, markdown: str, context: ConverterContext
    ) -> str:
        if not context.option("convert_math", instance_default=self._default_enabled):
            return markdown
        markdown = _MATH_BLOCK_RE.sub(
            lambda m: _render_block(m.group("body")), markdown
        )
        markdown = _MATH_INLINE_RE.sub(
            lambda m: _render_inline(m.group("body")), markdown
        )
        return markdown


def _render_block(body: str) -> str:
    """Wrap a cleaned math block body in ``$$ ... $$``."""
    return f"$${_strip_br(body)}$$"


def _render_inline(body: str) -> str:
    """Wrap a cleaned inline math body in ``$ ... $``."""
    return f"${_strip_br(body)}$"


def _strip_br(body: str) -> str:
    """Drop ``<br/>`` / ``<br>`` tags from a math body."""
    return _BR_TAG_RE.sub(" ", body).strip()


__all__ = ["MathStrategy"]