"""Updater for Rio."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class RioUpdater(GitHubReleaseAssetURLsUpdater):
    """Track Rio macOS DMG assets from GitHub latest releases."""

    name = "rio"
    GITHUB_OWNER = "raphamorim"
    GITHUB_REPO = "rio"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "rio.dmg"
