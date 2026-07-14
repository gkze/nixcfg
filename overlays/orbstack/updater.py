"""Updater for the pinned OrbStack macOS app archives."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import (
    PinnedSourceDownloadUpdater,
    VersionInfo,
    register_updater,
)


@register_updater
class OrbStackUpdater(PinnedSourceDownloadUpdater):
    """Pinned download updater for the OrbStack macOS app archives."""

    name = "orbstack"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "amd64",
    }

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Build the CDN URL from the release/build version components."""
        platform_value = self.PLATFORMS[platform]
        release, build = info.version.split("-", maxsplit=1)
        return (
            f"https://cdn-updates.orbstack.dev/{platform_value}/"
            f"OrbStack_v{release}_{build}_{platform_value}.dmg"
        )
