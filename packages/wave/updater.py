"""Updater for Wave."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import AssetURLsMetadata
from lib.update.updaters.vendor_feeds import fetch_electron_builder_asset_urls

if TYPE_CHECKING:
    import aiohttp


@register_updater
class WaveUpdater(DownloadHashUpdater):
    """Resolve Wave from its Electron update feed."""

    name = "wave"
    FEED_URL = "https://dl.waveterm.dev/releases-w2/latest-mac.yml"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Wave version and macOS DMG URL."""
        version, asset_urls = await fetch_electron_builder_asset_urls(
            session,
            self.FEED_URL,
            {
                "aarch64-darwin": lambda version, url: url.endswith(
                    f"Wave-darwin-arm64-{version}.dmg"
                ),
            },
            config=self.config,
        )
        return VersionInfo(version=version, metadata=AssetURLsMetadata(asset_urls))

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return Wave's metadata-provided DMG URL for the platform."""
        if isinstance(info.metadata, AssetURLsMetadata):
            url = info.metadata.asset_urls.get(platform)
            if isinstance(url, str) and url:
                return url
        arch = self.PLATFORMS[platform]
        return (
            f"https://dl.waveterm.dev/releases-w2/Wave-darwin-{arch}-{info.version}.dmg"
        )
