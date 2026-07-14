"""Updater for scie-pants preview launcher binaries."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import GitHubReleaseAssetURLsUpdater, register_updater


@register_updater
class PantsPreviewUpdater(GitHubReleaseAssetURLsUpdater):
    """Track scie-pants launcher binaries from GitHub latest releases."""

    name = "pants-preview"
    GITHUB_OWNER = "pantsbuild"
    GITHUB_REPO = "scie-pants"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "macos-aarch64",
        "aarch64-linux": "linux-aarch64",
        "x86_64-linux": "linux-x86_64",
    }
    ASSET_NAME_TEMPLATE: ClassVar[str] = "scie-pants-{platform_value}"
