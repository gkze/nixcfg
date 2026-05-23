"""Updater for Yaak beta."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters.base import register_updater
from lib.update.updaters.github_release import GitHubReleaseAssetURLsUpdater


@register_updater
class YaakBetaUpdater(GitHubReleaseAssetURLsUpdater):
    """Resolve Yaak's macOS DMG from its upstream GitHub release assets."""

    name = "yaak-beta"
    GITHUB_OWNER = "mountain-loop"
    GITHUB_REPO = "yaak"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "aarch64",
    }

    def _asset_name(self, version: str, platform_value: str) -> str:
        """Return the expected Yaak macOS asset name."""
        return f"Yaak_{version}_{platform_value}.dmg"
