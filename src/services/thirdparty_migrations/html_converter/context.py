"""Shared read-only bag handed to every conversion strategy.

Each strategy receives the same :class:`ConverterContext` so the
URL builders, the configured ``bodywidth`` and a pre-built
:class:`html2text.HTML2Text` instance are shared across the
pipeline.  Strategies are not allowed to mutate the context --
state that has to survive between the pre-process and the
post-process pass is carried through the pipeline's state dict
instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

import html2text

from .options import AttachmentUrlBuilder, ConvertOptions

if TYPE_CHECKING:
    from .pipeline import ConvertPipeline


@dataclass
class ConverterContext:
    """Read-only bag shared by every strategy in one conversion.

    Attributes:
        url_builder: turns an attachment key into the inline image
            URL used for ``<img>`` and ``![alt]`` references that
            point at an image or PDF file.
        link_builder: turns an attachment key into the URL used by
            the markdown link rendered for non-image / non-PDF
            files.  Falls back to ``url_builder`` when the caller
            did not configure a separate builder.
        bodywidth: forwarded to :func:`html2text.html2text`.  ``0``
            disables line wrapping (we want the resulting Markdown
            to match the project's "no auto-wrap" style).
        converter: the pre-built :class:`html2text.HTML2Text`
            instance every strategy can reuse so we don't rebuild
            it per strategy.
        options: the :class:`ConvertOptions` dict the caller
            passed to this conversion (or an empty mapping when
            none was provided).  Strategies should read options
            through :meth:`option` so per-call overrides always
            win over the instance defaults baked into the
            strategy.
        state: shared mutable dict strategies can use to carry
            data between the pre-process and post-process pass.
            Keys are strategy-namespaced (e.g.
            ``"code_blocks"``) to avoid collisions.
        pipeline: the owning :class:`ConvertPipeline`.  Exposed
            so a strategy (typically the details strategy) can
            run the rest of the postprocessor chain on a nested
            block it has just produced via :mod:`html2text`,
            without having to know about the strategies
            registered after it.
    """

    url_builder: AttachmentUrlBuilder
    link_builder: AttachmentUrlBuilder
    bodywidth: int
    converter: html2text.HTML2Text
    options: ConvertOptions
    state: Dict[str, Any] = field(default_factory=dict)
    pipeline: Optional["ConvertPipeline"] = None

    def option(
        self,
        key: str,
        *,
        instance_default: bool,
    ) -> bool:
        """Read a boolean :class:`ConvertOptions` key.

        Args:
            key: the option key (e.g. ``"convert_details"``).
            instance_default: the value to fall back to when the
                caller did not pass `options` and the key is not
                present in the dict.

        Returns:
            bool: `True` / `False` as a plain bool, so the
            caller can use it in a conditional without re-checking
            the truthiness of the raw dict value.
        """
        return bool(self.options.get(key, instance_default))


__all__ = ["ConverterContext"]