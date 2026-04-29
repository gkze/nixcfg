"""Updater for hwatch cargo vendor hash."""

from lib.update.updaters.base import cargo_vendor_updater

HwatchUpdater = cargo_vendor_updater("hwatch", module=__name__)
