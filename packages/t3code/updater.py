"""Updater registration for T3 Code's staged runtime Bun cache."""

from __future__ import annotations

from lib.update.updaters import register_updater
from lib.update.updaters.t3_runtime import T3RuntimeUpdater


@register_updater
class T3CodeUpdater(T3RuntimeUpdater):
    """Compute only the standalone T3 Code runtime ``node_modules`` hash."""

    name = "t3code"
    generated_artifact_files = ("bun.lock", "../t3code-desktop/bun.lock")
