"""Thin orchestrator that runs the default conversion pipeline.

:class:`BookstackHtmlConverter` owns a
:class:`ConvertPipeline` with the project's default set of
strategies pre-registered.  Each conversion just runs the
pipeline; per-conversion data (file_index, id_index, ...) is
bound via the :meth:`bind_file_index` / :meth:`bind_id_index`
methods the orchestrator calls between the upload and the
cross-ref second pass.

The legacy :meth:`rewrite_image_sources` / :meth:`rewrite_cross_references`
methods are kept as thin wrappers around
:class:`ImageRefStrategy` / :class:`CrossRefStrategy` so older
callers (and the orchestrator's second-pass loop) keep working
without touching the strategy wiring.
"""

from __future__ import annotations

from typing import Dict, Optional

from .options import AttachmentUrlBuilder, ConvertOptions
from .pipeline import ConvertPipeline
from .strategies import (
    CodeBlockStrategy,
    CrossRefStrategy,
    DetailsStrategy,
    ImageRefStrategy,
    MathStrategy,
)


class BookstackHtmlConverter:
    """HTML / Markdown converter for the BookStack importer.

    Args:
        attachment_url_builder: turns an attachment key into the
            URL that should replace an ``files/<filename>``
            reference.  Used for images and PDFs.
        attachment_link_builder: turns an attachment key into the
            URL used for general file attachments (anything that
            is not an image or PDF).  Defaults to
            :data:`attachment_url_builder`.
        bodywidth: forwarded to :func:`html2text.html2text`.  ``0``
            disables line wrapping.
        convert_details: default for the `convert_details` key in
            :class:`ConvertOptions`.  Defaults to `True`.
        convert_math: default for the `convert_math` key in
            :class:`ConvertOptions`.  Defaults to `True`.
        pipeline: optional pre-built :class:`ConvertPipeline`
            override (used by tests that want to swap a strategy
            out).  When `None` the converter builds a default
            pipeline with one instance of every built-in
            strategy.
    """

    def __init__(
        self,
        attachment_url_builder: AttachmentUrlBuilder,
        *,
        attachment_link_builder: Optional[AttachmentUrlBuilder] = None,
        bodywidth: int = 0,
        convert_details: bool = True,
        convert_math: bool = True,
        pipeline: Optional[ConvertPipeline] = None,
    ) -> None:
        self._url_builder = attachment_url_builder
        self._link_builder = attachment_link_builder or attachment_url_builder
        self._bodywidth = bodywidth
        self._convert_details = convert_details
        self._convert_math = convert_math

        if pipeline is None:
            pipeline = self._build_default_pipeline()

        self._pipeline = pipeline
        # The converter holds references to the
        # stateful-by-construction strategies so it can swap
        # the file_index / id_index in between conversions
        # without rebuilding the pipeline.
        self._image_refs = _find_strategy(
            self._pipeline, "image_refs", ImageRefStrategy
        )
        self._cross_refs = _find_strategy(
            self._pipeline, "cross_refs", CrossRefStrategy
        )

    def _build_default_pipeline(self) -> ConvertPipeline:
        """Build the default pipeline with one of every built-in strategy.

        The order matters: HTML preprocessors run in registration
        order so the outermost wrapper (e.g. ``<details>``) hides
        itself before an inner one (e.g. ``<pre><code>``) does;
        markdown postprocessors run in reverse so an inner
        placeholder is restored before the outer wrapper is
        rebuilt around it.
        """
        return (
            ConvertPipeline(
                url_builder=self._url_builder,
                link_builder=self._link_builder,
                bodywidth=self._bodywidth,
            )
            .with_preprocessor(CodeBlockStrategy())
            .with_preprocessor(DetailsStrategy(
                default_enabled=self._convert_details,
            ))
            .with_postprocessor(CrossRefStrategy(
                url_builder=self._url_builder,
            ))
            .with_postprocessor(ImageRefStrategy(
                url_builder=self._url_builder,
                link_builder=self._link_builder,
            ))
            .with_postprocessor(DetailsStrategy(
                default_enabled=self._convert_details,
            ))
            .with_postprocessor(CodeBlockStrategy())
            .with_postprocessor(MathStrategy(
                default_enabled=self._convert_math,
            ))
        )

    def html_to_markdown(
        self,
        html: str,
        *,
        options: Optional[ConvertOptions] = None,
    ) -> str:
        """Convert `html` to Markdown through the default pipeline.

        Empty / falsy input returns the empty string rather than
        :mod:`html2text`'s default ``"\n"`` so callers can fall
        through cleanly.

        Args:
            html: the HTML to convert.
            options: optional :class:`ConvertOptions` overrides
                for this single call (per-call wins over the
                instance defaults).

        Returns:
            str: the converted markdown body.
        """
        return self._pipeline.run(html, options=options)

    def convert_content(
        self,
        page,
        file_index: Dict[str, str],
        *,
        bsexport_index: Optional[Dict[int, str]] = None,
        attachment_meta: Optional[Dict[int, str]] = None,
        options: Optional[ConvertOptions] = None,
    ) -> str:
        """Pick the page's content source and run image-src rewrites.

        BookStack exports typically carry a ``markdown`` field
        that is non-empty for pages edited via the WYSIWYG; when
        empty we fall back to converting ``html``.  Both branches
        then go through :meth:`rewrite_image_sources` so the
        result references attachment URLs the importer will
        create.

        Args:
            page: the page whose content to convert (a
                :class:`~src.services.thirdparty_migrations.bookstack_models.BookstackPage`).
            file_index: mapping of original
                ``files/<filename>`` -> the new attachment key.
            bsexport_index: optional source id -> new attachment
                key map for inline ``[[bsexport:image:N]]`` /
                ``[[bsexport:attachment:N]]`` cross-refs.
            attachment_meta: optional source id -> original
                filename map for ``attachment`` kind cross-refs.
            options: optional :class:`ConvertOptions` overrides
                for this single call.

        Returns:
            str: the converted markdown body.
        """
        if page.markdown and page.markdown.strip():
            # BookStack saved the page as raw markdown (the
            # `markdown` field is non-empty); run the math
            # strategy on it directly so the BookStack KaTeX
            # delimiters (``\[ ... \]`` etc.) are converted to
            # the project's ``$$ ... $$`` / ``$ ... $`` form
            # before the result is persisted.  The HTML
            # preprocessors (code-block / details) are not
            # relevant here because the body is already
            # markdown -- only the markdown postprocessors run.
            body = self._run_math_postprocessor(
                page.markdown, options=options
            )
        elif page.html:
            body = self.html_to_markdown(page.html, options=options)
        else:
            return ""
        return self.rewrite_image_sources(
            body,
            file_index,
            bsexport_index=bsexport_index,
            attachment_meta=attachment_meta,
        )

    def rewrite_image_sources(
        self,
        content: str,
        file_index: Dict[str, str],
        *,
        bsexport_index: Optional[Dict[int, str]] = None,
        attachment_meta: Optional[Dict[int, str]] = None,
    ) -> str:
        """Replace ``files/<filename>`` references with attachment URLs.

        Thin wrapper around :class:`ImageRefStrategy`.  Kept on
        the converter for backward compatibility with the
        orchestrator's first-pass call site and the existing
        test suite.
        """
        strategy = ImageRefStrategy(
            url_builder=self._url_builder,
            link_builder=self._link_builder,
            file_index=file_index,
            bsexport_index=bsexport_index,
            attachment_meta=attachment_meta,
        )
        context = self._pipeline.build_context(None)
        return strategy.postprocess_markdown(content, context)

    def rewrite_cross_references(
        self,
        content: str,
        id_index: Dict[str, Dict[int, str]],
        attachment_url_builder: Optional[AttachmentUrlBuilder] = None,
        attachment_meta: Optional[Dict[int, str]] = None,
    ) -> str:
        """Rewrite ``[[bsexport:type:id]]`` cross-refs to attachment URLs.

        Thin wrapper around :class:`CrossRefStrategy`.  Kept on
        the converter for backward compatibility with the
        orchestrator's second-pass call site and the existing
        test suite.
        """
        strategy = CrossRefStrategy(
            url_builder=attachment_url_builder or self._url_builder,
            link_builder=self._link_builder,
            id_index=id_index,
            attachment_meta=attachment_meta,
        )
        context = self._pipeline.build_context(None)
        return strategy.postprocess_markdown(content, context)

    def _run_math_postprocessor(
        self,
        markdown: str,
        *,
        options: Optional[ConvertOptions] = None,
    ) -> str:
        """Run only the math postprocessor against raw markdown.

        Used by :meth:`convert_content` when a page's
        `markdown` field is non-empty so the BookStack KaTeX
        delimiters are converted to ``$$ ... $$`` /
        ``$ ... $`` even when the page never went through
        :mod:`html2text` (the html2text path picks the math
        strategy up automatically via
        :meth:`ConvertPipeline.run`).
        """
        strategy = self._pipeline.find_postprocessor("math")
        if strategy is None:
            return markdown
        context = self._pipeline.build_context(options)
        return strategy.postprocess_markdown(markdown, context)


def _find_strategy(
    pipeline: ConvertPipeline, name: str, expected_type: type
):
    """Return the postprocessor on `pipeline` whose `name` matches.

    Used by :class:`BookstackHtmlConverter` to grab references
    to the per-conversion-stateful strategies (image refs and
    cross refs) so it can reconfigure them between calls
    without rebuilding the whole pipeline.

    Raises:
        LookupError: no strategy on `pipeline` has the requested
            `name` (the converter cannot reconfigure itself and
            the caller should not have bypassed the default
            pipeline wiring).
    """
    strategy = pipeline.find_postprocessor(name)
    if strategy is None or not isinstance(strategy, expected_type):
        raise LookupError(
            f"ConvertPipeline has no postprocessor named {name!r} of "
            f"type {expected_type.__name__}; check the default pipeline wiring"
        )
    return strategy


__all__ = ["BookstackHtmlConverter"]