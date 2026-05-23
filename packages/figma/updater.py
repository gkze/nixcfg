"""Updater for Figma desktop macOS releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import AssetURLsMetadata

if TYPE_CHECKING:
    import aiohttp


@register_updater
class FigmaUpdater(DownloadHashUpdater):
    """Resolve Figma's per-arch ZIP URLs from Figma's release metadata."""

    name = "figma"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "mac-arm",
        "x86_64-darwin": "mac",
    }

    def _release_url(self, channel: str) -> str:
        return f"https://desktop.figma.com/{channel}/RELEASE.json?localVersion=0.0.0"

    async def _fetch_platform(
        self,
        session: aiohttp.ClientSession,
        channel: str,
    ) -> tuple[str, str]:
        url = self._release_url(channel)
        payload = await fetch_json(
            session,
            url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        data = json_utils.as_object_dict(payload, context=url)
        version = json_utils.get_required_str(data, "version", context=url)
        download_url = json_utils.get_required_str(data, "url", context=url)
        return version, download_url

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Figma version and per-platform ZIP URLs."""
        versions: dict[str, str] = {}
        asset_urls: dict[str, str] = {}
        for platform, channel in self.PLATFORMS.items():
            version, url = await self._fetch_platform(session, channel)
            versions[platform] = version
            asset_urls[platform] = url
        unique_versions = set(versions.values())
        if len(unique_versions) != 1:
            msg = f"Figma release metadata returned mismatched versions: {versions}"
            raise RuntimeError(msg)
        return VersionInfo(
            version=unique_versions.pop(),
            metadata=AssetURLsMetadata(asset_urls),
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return Figma's metadata-provided ZIP URL for the platform."""
        if isinstance(info.metadata, AssetURLsMetadata):
            url = info.metadata.asset_urls.get(platform)
            if isinstance(url, str) and url:
                return url
        channel = self.PLATFORMS[platform]
        return f"https://desktop.figma.com/{channel}/Figma-{info.version}.zip"
