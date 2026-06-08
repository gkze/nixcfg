"""Updater for Goose desktop's pinned Node dependency cache."""

from __future__ import annotations

from lib.update.updaters.base import bun_node_modules_updater

GooseDesktopUpdater = bun_node_modules_updater(
    "goose-desktop",
    supported_platforms=("aarch64-darwin",),
    module=__name__,
)
