"""Updater for hwatch cargo vendor hash."""

from lib.update.updaters import CargoVendorHashUpdater, register_updater


@register_updater
class HwatchUpdater(CargoVendorHashUpdater):
    """Cargo vendor hash updater for hwatch."""

    name = "hwatch"
