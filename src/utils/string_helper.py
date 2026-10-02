from typing import Any

DEFAULT_LOG_LIMIT: int = 50


def truncate(value: Any, limit: int = DEFAULT_LOG_LIMIT) -> str:
    """Truncate a string value to ``limit`` chars for safe logging."""
    if isinstance(value, str):
        return f"{value[:limit]}..." if len(value) > limit else value
    return str(value)