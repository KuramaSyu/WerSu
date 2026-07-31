"""Per-conversion knobs for the BookStack HTML -> Markdown step.

The shape is a :class:`TypedDict` so callers can pass
``ConvertOptions(convert_details=True)`` literals without
subclassing, while the
:class:`~src.services.thirdparty_migrations.html_converter.strategy.MarkdownPostprocessorStrategy`
classes below can read individual keys without re-validating the
mapping.

Adding a new flag
-----------------

1. Add the key here with a one-line docstring describing the
   default and the trade-off.
2. Read it through :meth:`ConverterContext.option` (which falls
   back to the strategy's own default) so the per-call override
   always wins over the instance default.
3. Extend the default :class:`ConvertPipeline` in
   :mod:`.converter` if a new strategy needs to be wired up
   based on the new flag.
"""

from __future__ import annotations

from typing import Callable, TypedDict


AttachmentUrlBuilder = Callable[[str], str]
"""Callable that turns an attachment key into a displayable URL."""


class ConvertOptions(TypedDict, total=False):
    """Per-conversion knobs for the BookStack HTML -> Markdown step.

    Every key is optional.  Callers can either pass a
    ``ConvertOptions`` literal directly to
    :meth:`BookstackHtmlConverter.convert_content` or set the
    matching field on the converter instance once at construction
    time.

    Keys:
        convert_details: when `True`, every ``<details>...</details>``
            block in the source HTML is preserved as a
            ``<details><summary>...</summary>...</details>``
            fragment in the resulting Markdown so the markdown
            viewer can collapse it.  Defaults to `True` -- the
            legacy ``html2text`` behaviour of stripping the
            wrapper is rarely what the import caller wants.
        convert_math: when `True`, the BookStack math syntax
            (the block-form open/close delimiters written as a
            backslash followed by ``[`` or ``]``, and the inline
            form with backslash ``(`` / ``)``, with embedded
            ``<br/>`` tags collapsed) is rewritten to the
            project's standard ``$$ ... $$`` and ``$ ... $``
            delimiters.  Defaults to `True`.
    """

    convert_details: bool
    convert_math: bool


__all__ = ["AttachmentUrlBuilder", "ConvertOptions"]