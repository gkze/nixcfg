"""Updater registration for T3 Code Desktop's staged runtime Bun cache."""

from __future__ import annotations

from lib.update.updaters import register_updater
from lib.update.updaters.t3_runtime import T3RuntimeUpdater


@register_updater
class T3CodeDesktopUpdater(T3RuntimeUpdater):
    """Compute only the desktop runtime ``node_modules`` hash."""

    name = "t3code-desktop"
    generated_artifact_files = ("../t3code/bun.lock", "bun.lock")
