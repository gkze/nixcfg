"""Updater for emdash's platform-specific npm dependency hashes."""

from lib.update.updaters import NpmDepsHashUpdater, register_updater


@register_updater
class EmdashUpdater(NpmDepsHashUpdater):
    """Npm deps hash updater for emdash."""

    name = "emdash"
    platform_specific = True
