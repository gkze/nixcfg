"""Updater for Agentlog macOS releases."""

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class AgentlogUpdater(GitHubReleaseAssetURLsUpdater):
    """Track Agentlog's signed arm64 DMG from GitHub latest releases."""

    name = "agentlog"
    GITHUB_OWNER = "jordienr"
    GITHUB_REPO = "agentlog-releases"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "aarch64",
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "Agentlog_{version}_{platform_value}.dmg"
