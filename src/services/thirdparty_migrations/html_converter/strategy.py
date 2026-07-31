"""Abstract base classes / Protocols for the conversion strategies.

The pipeline runs in two phases:

1. Every :class:`HtmlPreprocessorStrategy` rewrites the source
   HTML in order (so a code-block / details strategy can hide
   itself behind a placeholder before :mod:`html2text` sees the
   body, and a math strategy can replace ``<br/>`` inside math
   blocks before html2text turns them into stray line breaks).
2. After :func:`html2text.html2text` has produced the markdown,
   every :class:`MarkdownPostprocessorStrategy` rewrites the
   result in reverse order -- that way nested placeholders
   restored by an outer strategy don't get clobbered by an inner
   one.

A strategy can implement either protocol (or both).  Implement
only the side you actually need; the pipeline skips the absent
side cleanly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .context import ConverterContext


@runtime_checkable
class HtmlPreprocessorStrategy(Protocol):
    """Strategy that rewrites the source HTML before :mod:`html2text`.

    Implementations should return the cleaned HTML along with an
    opaque state object that the matching post-processor (if any)
    can use to restore the original content.  The state is
    carried in :attr:`ConverterContext.state` under a strategy-
    specific key, so a single HTML preprocessor and its matching
    markdown postprocessor are coupled by name rather than by
    constructor wiring.
    """

    @property
    def name(self) -> str:
        """Stable identifier used as the state-dict key.

        Two strategies in the same pipeline must not share a name;
        the pipeline asserts this when it is built.
        """
        ...

    def preprocess_html(
        self, html: str, context: ConverterContext
    ) -> str:
        """Return the (possibly rewritten) source HTML.

        Args:
            html: the raw HTML the BookStack page exported.
            context: shared converter context (see
                :class:`ConverterContext`); read-only.

        Returns:
            str: the HTML that should be handed to
            :func:`html2text.html2text`.  Implementations typically
            stash the original snippets they hid behind placeholders
            into :attr:`ConverterContext.state` so the matching
            post-processor can restore them.
        """
        ...


@runtime_checkable
class MarkdownPostprocessorStrategy(Protocol):
    """Strategy that rewrites the markdown :mod:`html2text` produced.

    Implementations receive the post-`html2text` markdown plus
    the shared context (and can pull state out of
    :attr:`ConverterContext.state` if a matching HTML
    preprocessor hid snippets behind placeholders).

    Run in **reverse order** of the HTML preprocessors so nested
    placeholders (e.g. a code block inside a `<details>` body)
    are restored by the inner strategy before the outer one
    re-wraps the block.
    """

    @property
    def name(self) -> str:
        """Stable identifier (must match any HTML preprocessor that
        shares state with this strategy)."""
        ...

    def postprocess_markdown(
        self, markdown: str, context: ConverterContext
    ) -> str:
        """Return the (possibly rewritten) markdown body.

        Args:
            markdown: the markdown :func:`html2text.html2text`
                produced from the preprocessed HTML.
            context: shared converter context (see
                :class:`ConverterContext`); read-only.

        Returns:
            str: the markdown that should be returned to the
            caller (or fed into the next strategy).
        """
        ...


__all__ = ["HtmlPreprocessorStrategy", "MarkdownPostprocessorStrategy"]