"""Preserve ``<details><summary>...</summary>...</details>`` blocks.

The default :mod:`html2text` strips ``<details>`` and emits the
body text immediately below the summary, so the rendered note
loses the collapsible wrapper.

When ``options["convert_details"]`` is `True`, this strategy hides
every ``<details>...</details>`` block behind an opaque
``[details:XXXXXXXX]`` placeholder before :mod:`html2text` runs,
then swaps it back in as a raw ``<details><summary>...</summary>...
</details>`` markdown fragment -- the markdown viewer passes the
HTML tags through verbatim, so the section stays collapsible.

The body is passed through :mod:`html2text` recursively (and
then through the rest of the postprocessor chain) so a nested
``<pre><code>`` snippet inside a `<details>` body picks up the
same fenced-code treatment the outer page gets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

import html2text

from ..context import ConverterContext
from ..strategy import HtmlPreprocessorStrategy, MarkdownPostprocessorStrategy


# Match ``<details ...><summary ...>title</summary>body</details>``.
# We accept attributes on both the wrapper and the summary; the
# `re.DOTALL` flag lets the body span multiple lines.  ``<summary>``
# is required -- a `<details>` without one is unusual in BookStack
# output and we let html2text fall back to its default for that.
_DETAILS_RE = re.compile(
    r"<details\b[^>]*>\s*<summary\b[^>]*>(?P<summary>.*?)</summary>(?P<body>.*?)</details>\s*",
    re.DOTALL | re.IGNORECASE,
)

_DETAILS_PLACEHOLDER_RE = re.compile(r"\[details:([0-9a-f]{8})\]")


@dataclass
class _DetailsBlock:
    """One ``<details><summary>...</summary>body</details>``.

    Both halves are stored as raw HTML so the converter can pass
    them through :mod:`html2text` recursively -- this lets nested
    code blocks / lists / paragraphs inside the body pick up the
    same fenced-code + image-rewrite treatment the outer page
    gets.
    """

    summary_html: str
    body_html: str


_STATE_KEY = "_details_blocks"


class DetailsStrategy(
    HtmlPreprocessorStrategy, MarkdownPostprocessorStrategy
):
    """Preserve ``<details>`` blocks as collapsible markdown fragments.

    Default behaviour: enabled.  Pass
    ``options={"convert_details": False}`` (or set
    ``convert_details=False`` on the converter constructor) to
    fall back to the legacy behaviour where :mod:`html2text`
    strips the wrapper.

    Implementation notes
    --------------------

    * The summary is run through :mod:`html2text` so any inline
      HTML inside it (e.g. ``<code>``) becomes markdown.
    * The body is run through :mod:`html2text` recursively and
      then through the rest of the postprocessor chain, so a
      ``<pre><code>`` snippet inside a `<details>` body still
      picks up the fenced-code treatment the outer page gets.
    * The body sits at column zero inside the wrapper -- the
      ``<details>`` / ``<summary>`` HTML tags themselves are
      what keep it inside the collapsible section in the
      markdown viewer, and indenting the body would break any
      nested fenced code block (the opening ```` ``` ````
      would no longer be at column 0).
    """

    def __init__(self, *, default_enabled: bool = True) -> None:
        self._default_enabled = default_enabled

    @property
    def name(self) -> str:
        return "details"

    def preprocess_html(
        self, html: str, context: ConverterContext
    ) -> str:
        if not context.option(
            "convert_details", instance_default=self._default_enabled
        ):
            # Still need the state key so the post-processor is
            # a no-op (no placeholders to restore).
            context.state[_STATE_KEY] = {}
            return html

        blocks: Dict[str, _DetailsBlock] = {}
        context.state[_STATE_KEY] = blocks

        def replace(match: re.Match[str]) -> str:
            summary_html = match.group("summary").strip()
            body_html = match.group("body").strip()
            placeholder_id = f"{len(blocks):08x}"
            blocks[placeholder_id] = _DetailsBlock(
                summary_html=summary_html, body_html=body_html
            )
            return f"\n\n[details:{placeholder_id}]\n\n"

        return _DETAILS_RE.sub(replace, html)

    def postprocess_markdown(
        self, markdown: str, context: ConverterContext
    ) -> str:
        blocks: Dict[str, _DetailsBlock] = context.state.get(_STATE_KEY, {})
        if not blocks:
            return markdown

        def replace(match: re.Match[str]) -> str:
            block = blocks.get(match.group(1))
            if block is None:
                return match.group(0)
            return _render_block(block, context)

        return _DETAILS_PLACEHOLDER_RE.sub(replace, markdown)


def _render_block(
    block: _DetailsBlock, context: ConverterContext
) -> str:
    """Render one ``<details>`` block as a markdown fragment.

    The summary and body are each run through the shared
    :class:`html2text.HTML2Text` instance so any inline HTML
    (e.g. ``<code>``) becomes markdown.

    The body markdown is then run through the rest of the
    postprocessor chain (via
    :meth:`ConvertPipeline.run_postprocessors`) so a nested
    ``<pre><code>`` snippet inside a ``<details>`` body picks up
    the same fenced-code treatment the outer page gets.  We
    skip the current details postprocessor (which we *are*)
    so we don't recurse forever, and we skip preprocessors
    because the body is already HTML-converted.
    """
    converter = context.converter
    summary_md = converter.handle(block.summary_html).strip()
    body_md = converter.handle(block.body_html).strip()
    if context.pipeline is not None:
        body_md = context.pipeline.run_postprocessors(
            body_md, context, skip=["details"]
        )
    return (
        f"<details>\n"
        f"<summary>{summary_md}</summary>\n\n"
        f"{body_md}\n\n"
        f"</details>"
    )


__all__ = ["DetailsStrategy"]