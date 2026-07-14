"""Updater for the gogcli Go vendor hash."""

from lib.update.updaters import GoVendorHashUpdater, register_updater


@register_updater
class GogcliUpdater(GoVendorHashUpdater):
    """Go vendor hash updater for gogcli."""

    name = "gogcli"
