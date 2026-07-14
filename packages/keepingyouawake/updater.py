"""Updater for KeepingYouAwake macOS ZIP releases."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class KeepingYouAwakeUpdater(GitHubReleaseAssetURLsUpdater):
    """Track KeepingYouAwake ZIP assets from GitHub latest releases."""

    name = "keepingyouawake"
    GITHUB_OWNER = "newmarcel"
    GITHUB_REPO = "KeepingYouAwake"
    TAG_PREFIX = ""
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "KeepingYouAwake-{version}.zip"
