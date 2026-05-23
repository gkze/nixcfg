"""Updater for KeepingYouAwake macOS ZIP releases."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters.base import register_updater
from lib.update.updaters.github_release import GitHubReleaseAssetURLsUpdater


@register_updater
class KeepingYouAwakeUpdater(GitHubReleaseAssetURLsUpdater):
    """Track the latest KeepingYouAwake GitHub release ZIP."""

    name = "keepingyouawake"
    GITHUB_OWNER = "newmarcel"
    GITHUB_REPO = "KeepingYouAwake"
    TAG_PREFIX = ""
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    def _asset_name(self, version: str, platform_value: str) -> str:
        _ = platform_value
        return f"KeepingYouAwake-{version}.zip"
