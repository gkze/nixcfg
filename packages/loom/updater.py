"""Updater for Loom desktop macOS releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import ElectronBuilderAssetURLsUpdater, register_updater
from lib.update.updaters.metadata import AssetURLsMetadata

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lib.update.updaters import VersionInfo


@register_updater
class LoomUpdater(ElectronBuilderAssetURLsUpdater):
    """Track Loom desktop DMG assets from the electron-builder feed."""

    name = "loom"
    FEED_URL: ClassVar[str] = (
        "https://packages.loom.com/desktop-packages/latest-mac.yml"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        "aarch64-darwin": lambda _version, url: url.endswith("-arm64.dmg"),
        "x86_64-darwin": lambda version, url: url.endswith(f"Loom-{version}.dmg"),
    }

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Prefer the feed-resolved asset URL, then the conventional CDN URL."""
        if isinstance(info.metadata, AssetURLsMetadata):
            url = info.metadata.asset_urls.get(platform)
            if isinstance(url, str) and url:
                return url
        suffix = "-arm64" if self.PLATFORMS[platform] == "arm64" else ""
        return (
            "https://packages.loom.com/desktop-packages/"
            f"Loom-{info.version}{suffix}.dmg"
        )
