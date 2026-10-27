"""Updater for hwatch cargo vendor hash."""

from lib.update.updaters import CargoVendorHashUpdater, register_updater


@register_updater
class HwatchUpdater(CargoVendorHashUpdater):
    """Refresh hwatch from the cargo vendor staging fixed output."""

    name = "hwatch"
