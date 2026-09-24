"""Concrete :class:`BackgroundProcessABC` implementations.

User-action pair plus the one-shot
:class:`~.missing_embedding_process.MissingEmbeddingProcessImpl` that
backfills embeddings on startup.
"""

from .missing_embedding_process import MissingEmbeddingProcessImpl  # noqa: F401
from .user_disable_process import UserDisableProcessImpl  # noqa: F401
from .user_enable_process import UserEnableProcessImpl  # noqa: F401