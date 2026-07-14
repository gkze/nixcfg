"""Updater for the axiom-cli Go vendor hash."""

from lib.update.updaters import GoVendorHashUpdater, register_updater


@register_updater
class AxiomCliUpdater(GoVendorHashUpdater):
    """Go vendor hash updater for axiom-cli."""

    name = "axiom-cli"
