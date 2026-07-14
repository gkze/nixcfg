"""Updater for Freelens."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class FreelensUpdater(GitHubReleaseAssetURLsUpdater):
    """Track Freelens macOS DMG assets from GitHub latest releases."""

    name = "freelens"
    GITHUB_OWNER = "freelensapp"
    GITHUB_REPO = "freelens"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "Freelens-{version}-macos-{platform_value}.dmg"
