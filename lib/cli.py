"""Shared CLI defaults for Typer/Click applications."""

from __future__ import annotations

from typing import Final

HELP_CONTEXT_SETTINGS: Final[dict[str, list[str]]] = {
    "help_option_names": ["-h", "--help"],
}
