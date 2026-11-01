"""Updater for the neorex Rex pane binary."""

from lib.update.updaters import GitHubHeadSourceUpdater, register_updater


@register_updater
class NeorexUpdater(GitHubHeadSourceUpdater):
    """Track the neorex default-branch head; upstream tags no releases."""

    name = "neorex"
    GITHUB_OWNER = "alexrlocke"
    GITHUB_REPO = "neorex"
    supported_platforms = ("aarch64-darwin",)
