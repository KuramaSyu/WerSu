"""Concrete :class:`BackgroundProcessABC` implementations.

Currently only the user-action pair is here; attachment GC and the
share-expiry process are future work and land in follow-up PRs that
use this scaffolding.
"""

from .user_disable_process import UserDisableProcessImpl  # noqa: F401
from .user_enable_process import UserEnableProcessImpl  # noqa: F401