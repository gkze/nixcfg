"""Updater for Rio."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters.base import register_updater
from lib.update.updaters.github_release import GitHubReleaseAssetURLsUpdater


@register_updater
class RioUpdater(GitHubReleaseAssetURLsUpdater):
    """Resolve Rio from its upstream GitHub release assets."""

    name = "rio"
    GITHUB_OWNER = "raphamorim"
    GITHUB_REPO = "rio"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
    }

    def _asset_name(self, version: str, platform_value: str) -> str:
        """Return the expected Rio macOS asset name."""
        _ = (version, platform_value)
        return "rio.dmg"
