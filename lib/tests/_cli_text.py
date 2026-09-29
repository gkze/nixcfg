"""Helpers for asserting CLI help text under Rich/TTY geometry drift."""

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*[mK]")


def visible_cli_text(text: str) -> str:
    """Flatten Rich/TTY help so flag assertions survive hosted Darwin width."""
    stripped = _ANSI.sub("", text)
    return re.sub(r"\s+", " ", re.sub(r"(?<=-)\s+", "", stripped))
