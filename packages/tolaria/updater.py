"""Updater for the pinned Tolaria macOS app archive."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import (
    PinnedSourceDownloadUpdater,
    VersionInfo,
    register_updater,
)


@register_updater
class TolariaUpdater(PinnedSourceDownloadUpdater):
    """Pinned download updater for the Tolaria macOS app archive."""

    name = "tolaria"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "Silicon"}

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Build the release URL from the date-derived tag and platform."""
        version = info.version
        platform_value = self.PLATFORMS[platform]
        year, month, day = version.split(".", maxsplit=2)
        tag = f"v{year}-{int(month):02d}-{int(day):02d}"
        return (
            f"https://github.com/refactoringhq/tolaria/releases/download/{tag}/"
            f"Tolaria_{version}_macOS_{platform_value}.app.tar.gz"
        )
