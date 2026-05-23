"""Updater for scie-pants preview launcher binaries."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters.base import register_updater
from lib.update.updaters.github_release import GitHubReleaseAssetURLsUpdater


@register_updater
class PantsPreviewUpdater(GitHubReleaseAssetURLsUpdater):
    """Track scie-pants release binaries for supported platforms."""

    name = "pants-preview"
    GITHUB_OWNER = "pantsbuild"
    GITHUB_REPO = "scie-pants"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "macos-aarch64",
        "aarch64-linux": "linux-aarch64",
        "x86_64-linux": "linux-x86_64",
    }

    def _asset_name(self, version: str, platform_value: str) -> str:
        _ = version
        return f"scie-pants-{platform_value}"
