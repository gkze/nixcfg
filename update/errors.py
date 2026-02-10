"""Error formatting utilities for update operations."""

import traceback


def format_exception(exc: Exception) -> str:
    """Return the exception message with the current traceback text."""
    return f"{exc}\n{traceback.format_exc()}"
