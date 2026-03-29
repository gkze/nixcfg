"""Updater for mux's platform-specific Bun offline cache hashes."""

from lib.update.updaters.base import bun_node_modules_updater

MuxUpdater = bun_node_modules_updater("mux", module=__name__)
