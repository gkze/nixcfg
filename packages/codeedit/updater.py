"""Updater for CodeEdit macOS DMG releases."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class CodeEditUpdater(GitHubReleaseAssetURLsUpdater):
    """Track CodeEdit DMG assets from GitHub latest releases."""

    name = "codeedit"
    GITHUB_OWNER = "CodeEditApp"
    GITHUB_REPO = "CodeEdit"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "CodeEdit.dmg"
