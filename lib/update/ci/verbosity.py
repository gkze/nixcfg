"""Shared CLI verbosity translation for Nix-backed CI commands."""

from __future__ import annotations

import logging


def python_log_level(verbosity: int) -> int:
    """Map CLI verbosity count to the Python logging level."""
    return logging.DEBUG if verbosity > 0 else logging.INFO


def nix_verbosity_args(verbosity: int) -> list[str]:
    """Return Nix CLI verbosity arguments for *verbosity*."""
    return [] if verbosity <= 0 else ["-" + ("v" * verbosity)]


def nix_verbosity_from_cli(verbosity: int) -> int:
    """Reserve the first ``-v`` for Python logs and forward the remainder."""
    return max(0, verbosity - 1)


__all__ = ["nix_verbosity_args", "nix_verbosity_from_cli", "python_log_level"]
