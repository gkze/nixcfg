"""Updater for T3 Code Desktop's staged runtime Bun cache."""

from lib.update.updaters.base import bun_node_modules_updater

T3CodeDesktopUpdater = bun_node_modules_updater(
    "t3code-desktop",
    input_name="t3code",
    module=__name__,
)
