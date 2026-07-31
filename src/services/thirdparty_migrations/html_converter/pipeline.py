"""Ordered pipeline that runs the conversion strategies.

The pipeline owns two ordered lists:

* ``preprocessors`` -- every :class:`HtmlPreprocessorStrategy`
  the pipeline should run before :mod:`html2text`, in registration
  order.
* ``postprocessors`` -- every :class:`MarkdownPostprocessorStrategy`
  the pipeline should run after :mod:`html2text`, in reverse
  registration order (so nested placeholders are restored
  inside-out).

A strategy can implement either protocol, or both.  The pipeline
re-orders its postprocessor list at build time so that HTML
preprocessors and their matching markdown postprocessors always
end up paired (outer-most last).
"""

from __future__ import annotations

import html2text
from typing import Iterable, List, Optional

from .context import ConverterContext
from .options import AttachmentUrlBuilder, ConvertOptions
from .strategy import (
    HtmlPreprocessorStrategy,
    MarkdownPostprocessorStrategy,
)


class ConvertPipeline:
    """Ordered list of strategies plus the shared :mod:`html2text` setup.

    Args:
        preprocessors: HTML preprocessor strategies, in the order
            they should run.  Use :meth:`with_preprocessor` to
            append one strategy at a time.
        postprocessors: Markdown postprocessor strategies.  Run in
            reverse of registration order -- append the *innermost*
            strategy last.
        url_builder: callable that turns an attachment key into
            the inline image URL used for image / PDF refs.
        link_builder: callable that turns an attachment key into
            the URL used by the markdown link rendered for
            non-image / non-PDF files.  Defaults to
            `url_builder` so older callers and tests that only
            configure the image URL still work.
        bodywidth: forwarded to :func:`html2text.html2text`.  ``0``
            disables line wrapping (we want the resulting Markdown
            to match the project's "no auto-wrap" style).
    """

    def __init__(
        self,
        *,
        preprocessors: Optional[Iterable[HtmlPreprocessorStrategy]] = None,
        postprocessors: Optional[Iterable[MarkdownPostprocessorStrategy]] = None,
        url_builder: AttachmentUrlBuilder,
        link_builder: Optional[AttachmentUrlBuilder] = None,
        bodywidth: int = 0,
    ) -> None:
        self._url_builder = url_builder
        self._link_builder = link_builder or url_builder
        self._bodywidth = bodywidth
        self._preprocessors: List[HtmlPreprocessorStrategy] = list(
            preprocessors or []
        )
        self._postprocessors: List[MarkdownPostprocessorStrategy] = list(
            postprocessors or []
        )
        self._validate_unique_names()

    def with_preprocessor(
        self, strategy: HtmlPreprocessorStrategy
    ) -> "ConvertPipeline":
        """Append an HTML preprocessor strategy; returns `self`."""
        self._preprocessors.append(strategy)
        self._validate_unique_names()
        return self

    def with_postprocessor(
        self, strategy: MarkdownPostprocessorStrategy
    ) -> "ConvertPipeline":
        """Append a markdown postprocessor strategy; returns `self`."""
        self._postprocessors.append(strategy)
        self._validate_unique_names()
        return self

    def build_context(
        self, options: Optional[ConvertOptions]
    ) -> ConverterContext:
        """Build the shared :class:`ConverterContext` for one conversion.

        Args:
            options: the :class:`ConvertOptions` the caller passed
                to the converter (or `None` for "use defaults").

        Returns:
            ConverterContext: the fresh context every strategy
            receives for this conversion.  The pipeline constructs
            a new :class:`html2text.HTML2Text` instance every
            call so strategies can't accidentally leak state
            between conversions.  The context also carries a
            back-reference to ``self`` so a strategy can run the
            rest of the postprocessor chain on a nested block
            (see :meth:`ConvertPipeline.run_postprocessors`).
        """
        converter = html2text.HTML2Text()
        converter.bodywidth = self._bodywidth
        converter.ignore_links = False
        converter.ignore_images = False
        return ConverterContext(
            url_builder=self._url_builder,
            link_builder=self._link_builder,
            bodywidth=self._bodywidth,
            converter=converter,
            options=options or ConvertOptions(),
            state={},
            pipeline=self,
        )

    def run(
        self, html: str, *, options: Optional[ConvertOptions] = None
    ) -> str:
        """Run the full pipeline on `html`.

        Empty / falsy input returns the empty string rather than
        :mod:`html2text`'s default ``"\n"`` so callers can fall
        through cleanly.

        Args:
            html: the source HTML.  Empty / falsy returns
                ``""`` without running any strategy.
            options: optional :class:`ConvertOptions` overrides
                for this single call.

        Returns:
            str: the converted markdown body.
        """
        if not html:
            return ""

        context = self.build_context(options)

        for pre in self._preprocessors:
            html = pre.preprocess_html(html, context)

        markdown = context.converter.handle(html).strip()

        markdown = self.run_postprocessors(markdown, context)
        return markdown

    def run_postprocessors(
        self,
        markdown: str,
        context: ConverterContext,
        *,
        skip: Optional[Iterable[str]] = None,
    ) -> str:
        """Run every markdown postprocessor in reverse order.

        Used by the details strategy to run the rest of the
        postprocessor chain on a `<details>` body that has
        already been converted by :mod:`html2text` -- so a
        nested ``<pre><code>`` snippet inside the body picks up
        the same fenced-code treatment the outer page gets.

        Args:
            markdown: the markdown to post-process.
            context: the shared :class:`ConverterContext` for this
                conversion (carries the html2text instance,
                state, and option overrides).
            skip: optional iterable of strategy names to exclude
                from this run (used by the details strategy to
                avoid recursing into itself).

        Returns:
            str: the post-processed markdown.
        """
        skip_set = set(skip or ())
        for post in reversed(self._postprocessors):
            if post.name in skip_set:
                continue
            markdown = post.postprocess_markdown(markdown, context)
        return markdown

    def find_postprocessor(self, name: str) -> Optional[MarkdownPostprocessorStrategy]:
        """Return the first registered postprocessor whose `name` matches.

        Used by :class:`BookstackHtmlConverter` to grab references
        to the per-conversion-stateful strategies so it can
        reconfigure them between calls without rebuilding the
        whole pipeline.

        Args:
            name: the strategy's :attr:`name`.

        Returns:
            The strategy instance, or `None` if no postprocessor
            on the pipeline has that name.
        """
        for post in self._postprocessors:
            if post.name == name:
                return post
        return None

        # Postprocessors run in reverse order so nested
        # placeholders are restored inside-out (a code block
        # hidden inside a `<details>` body gets its fence back
        # before the outer `<details>` wraps the block).
        for post in reversed(self._postprocessors):
            markdown = post.postprocess_markdown(markdown, context)

        return markdown

    def _validate_unique_names(self) -> None:
        """Assert no two strategies in the same phase share a name.

        Strategies share state via
        :attr:`ConverterContext.state` keyed by ``strategy.name``,
        so a name collision inside one phase would silently
        clobber state between strategies.  A single strategy
        registered as both preprocessor and postprocessor is
        allowed (it pairs its own state in the same conversion
        via the shared ``ConverterContext``); the check is
        "unique within each list", not "unique globally".
        """
        for phase, label in (
            (self._preprocessors, "preprocessor"),
            (self._postprocessors, "postprocessor"),
        ):
            names = [s.name for s in phase]
            if len(names) != len(set(names)):
                raise ValueError(
                    f"ConvertPipeline {label}s must have unique names; got "
                    f"{names!r}"
                )


__all__ = ["ConvertPipeline"]