"""Updater for the gogcli Go vendor hash."""

from lib.update.updaters import register_updater
from lib.update.updaters.go_compatibility import GoModCompatibilityUpdater


@register_updater
class GogcliUpdater(GoModCompatibilityUpdater):
    """Go vendor hash updater for gogcli."""

    name = "gogcli"
    GITHUB_OWNER = "steipete"
    GITHUB_REPO = "gogcli"
