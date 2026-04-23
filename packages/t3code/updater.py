"""Updater for T3 Code's platform-specific Bun workspace cache."""

from lib.update.updaters.base import bun_node_modules_updater

T3CodeUpdater = bun_node_modules_updater(
    "t3code",
    input_name="t3code",
    module=__name__,
)
