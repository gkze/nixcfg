"""Updater for CodeEdit macOS DMG releases."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters.base import register_updater
from lib.update.updaters.github_release import GitHubReleaseAssetURLsUpdater


@register_updater
class CodeEditUpdater(GitHubReleaseAssetURLsUpdater):
    """Track the latest CodeEdit GitHub release DMG."""

    name = "codeedit"
    GITHUB_OWNER = "CodeEditApp"
    GITHUB_REPO = "CodeEdit"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    def _asset_name(self, version: str, platform_value: str) -> str:
        _ = (version, platform_value)
        return "CodeEdit.dmg"
