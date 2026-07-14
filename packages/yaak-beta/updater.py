"""Updater for Yaak beta."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class YaakBetaUpdater(GitHubReleaseAssetURLsUpdater):
    """Track Yaak beta macOS DMG assets from GitHub latest releases."""

    name = "yaak-beta"
    GITHUB_OWNER = "mountain-loop"
    GITHUB_REPO = "yaak"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "aarch64",
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "Yaak_{version}_{platform_value}.dmg"
