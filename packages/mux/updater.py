"""Updater for mux's platform-specific Bun offline cache hashes."""

from lib.update.updaters import BunNodeModulesHashUpdater, register_updater


@register_updater
class MuxUpdater(BunNodeModulesHashUpdater):
    """Bun node_modules hash updater for mux."""

    name = "mux"
