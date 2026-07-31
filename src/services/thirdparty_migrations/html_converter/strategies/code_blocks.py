"""Convert ``<pre><code>...</code></pre>`` blocks to fenced code blocks.

The default :func:`html2text.html2text` emits 4-space indented
code for ``<pre><code>`` snippets.  The project's markdown viewer
does not recognise those as code blocks, so the resulting note
shows the code region as an empty fence.

This strategy hides every ``<pre><code>...</code></pre>`` block
behind an opaque ``[code:XXXXXXXX]`` placeholder before
:mod:`html2text` runs, then swaps the placeholder back in as a
proper fenced `````X\n...\n``` `` block afterwards.  The language
hint from ``class="language-X"`` is preserved so the renderer
can pick the right syntax highlighter.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from typing import Dict

from ..context import ConverterContext
from ..strategy import HtmlPreprocessorStrategy, MarkdownPostprocessorStrategy


# Match ``<pre><code class="language-X">...</code></pre>`` or
# the no-language variant.  BookStack always emits an `id`
# attribute on the `<pre>` and a `language-X` class on the
# `<code>`; we tolerate both with the optional groups.
# ``re.DOTALL`` makes the body match span multiple lines so
# multi-line scripts and config blocks survive intact.
_CODE_BLOCK_RE = re.compile(
    r"<pre\b[^>]*>\s*<code\b[^>]*?(?:\s+class=\"[^\"]*language-(?P<lang>[a-zA-Z0-9_+\-]+)[^\"]*\")?[^>]*>"
    r"(?P<body>.*?)"
    r"</code>\s*</pre>",
    re.DOTALL | re.IGNORECASE,
)

# Placeholder text the strategy leaves in the cleaned HTML /
# markdown.  Deliberately an unlikely sequence of characters so
# it cannot collide with anything BookStack would emit.
_CODE_PLACEHOLDER_RE = re.compile(r"\[code:([0-9a-f]{8})\]")


@dataclass
class _CodeBlock:
    """One ``<pre><code class="language-X">body</code></pre>``.

    `lang` may be empty when the source did not carry a
    ``language-X`` class; in that case the restored fence is
    emitted without a language hint.
    """

    lang: str
    body: str


_STATE_KEY = "_code_blocks"


class CodeBlockStrategy(
    HtmlPreprocessorStrategy, MarkdownPostprocessorStrategy
):
    """Hide ``<pre><code>`` behind a placeholder, then restore as a fence.

    The strategy implements both protocols so it can stash the
    original code-block bodies in
    :attr:`ConverterContext.state` during the HTML pass and
    restore them after :mod:`html2text` has produced the
    markdown.

    Default behaviour: enabled.
    """

    @property
    def name(self) -> str:
        return "code_blocks"

    def preprocess_html(
        self, html: str, context: ConverterContext
    ) -> str:
        blocks: Dict[str, _CodeBlock] = {}
        context.state[_STATE_KEY] = blocks

        def replace(match: re.Match[str]) -> str:
            lang = (match.group("lang") or "").strip()
            body = html_lib.unescape(match.group("body"))
            # Strip one trailing newline that BookStack's editor
            # always adds; the fenced-block closer handles the
            # real line break.
            if body.endswith("\n"):
                body = body[:-1]
            placeholder_id = f"{len(blocks):08x}"
            blocks[placeholder_id] = _CodeBlock(lang=lang, body=body)
            return f"\n\n[code:{placeholder_id}]\n\n"

        return _CODE_BLOCK_RE.sub(replace, html)

    def postprocess_markdown(
        self, markdown: str, context: ConverterContext
    ) -> str:
        blocks: Dict[str, _CodeBlock] = context.state.get(_STATE_KEY, {})

        def replace(match: re.Match[str]) -> str:
            block = blocks.get(match.group(1))
            if block is None:
                return match.group(0)
            if block.lang:
                return f"```{block.lang}\n{block.body}\n```"
            return f"```\n{block.body}\n```"

        return _CODE_PLACEHOLDER_RE.sub(replace, markdown)


__all__ = ["CodeBlockStrategy"]