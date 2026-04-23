"""Updater for the internal T3 Code workspace Bun dependency cache."""

from lib.update.updaters.base import bun_node_modules_updater

T3CodeWorkspaceUpdater = bun_node_modules_updater(
    "t3code-workspace",
    input_name="t3code",
    module=__name__,
)
