"""Concrete :class:`BackgroundProcessABC` implementations.

User-action pair, the one-shot
:class:`~.missing_embedding_process.MissingEmbeddingProcessImpl` that
backfills embeddings on startup, and the periodic
:class:`~.trash_unlinked_attachments_process.TrashUnlinkedAttachmentsProcessImpl`
that trashes and purges unlinked attachments.
"""

from .calculated_stats_process import (  # noqa: F401
    CalculatedStatsProcessImpl,
    stats_invalidation_listeners,
)
from .daily_version_compaction_process import (  # noqa: F401
    DailyVersionCompactionProcessImpl,
)
from .missing_embedding_process import MissingEmbeddingProcessImpl  # noqa: F401
from .trash_unlinked_attachments_process import (  # noqa: F401
    TrashUnlinkedAttachmentsProcessImpl,
)
from .user_disable_process import UserDisableProcessImpl  # noqa: F401
from .user_enable_process import UserEnableProcessImpl  # noqa: F401
