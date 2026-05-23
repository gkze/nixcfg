"""Updater for Loom desktop macOS releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import AssetURLsMetadata
from lib.update.updaters.vendor_feeds import fetch_electron_builder_asset_urls

if TYPE_CHECKING:
    import aiohttp


@register_updater
class LoomUpdater(DownloadHashUpdater):
    """Resolve Loom's per-arch DMG URLs from its Electron update feed."""

    name = "loom"
    FEED_URL = "https://packages.loom.com/desktop-packages/latest-mac.yml"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Loom version and per-platform DMG URLs."""
        version, asset_urls = await fetch_electron_builder_asset_urls(
            session,
            self.FEED_URL,
            {
                "aarch64-darwin": lambda _version, url: url.endswith("-arm64.dmg"),
                "x86_64-darwin": lambda version, url: url.endswith(
                    f"Loom-{version}.dmg"
                ),
            },
            config=self.config,
        )
        return VersionInfo(version=version, metadata=AssetURLsMetadata(asset_urls))

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return Loom's metadata-provided DMG URL for the platform."""
        if isinstance(info.metadata, AssetURLsMetadata):
            url = info.metadata.asset_urls.get(platform)
            if isinstance(url, str) and url:
                return url
        arch = self.PLATFORMS[platform]
        suffix = "-arm64" if arch == "arm64" else ""
        return f"https://packages.loom.com/desktop-packages/Loom-{info.version}{suffix}.dmg"
