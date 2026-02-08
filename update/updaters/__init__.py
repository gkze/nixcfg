"""Updater registry and built-in definitions."""

from update.updaters import builtin as _builtin  # noqa: F401
from update.updaters.base import UPDATERS, Updater

__all__ = ["UPDATERS", "Updater"]
