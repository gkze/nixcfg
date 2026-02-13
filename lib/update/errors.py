"""Error formatting utilities for update operations."""

from __future__ import annotations

import traceback


def format_exception(exc: Exception, *, include_traceback: bool = False) -> str:
    """Return an exception message with optional traceback details."""
    message = str(exc)
    if not message:
        message = exc.__class__.__name__

    if not include_traceback:
        return message

    traceback_lines = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    return f"{message}\n{traceback_lines}"
