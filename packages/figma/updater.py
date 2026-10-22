"""Updater for Figma desktop macOS releases."""

from typing import TYPE_CHECKING, ClassVar
from urllib.parse import quote

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.core import verify_platform_versions
from lib.update.updaters.metadata import AssetURLsMetadata

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.core import UpdateContext


@register_updater
class FigmaUpdater(DownloadHashUpdater):
    """Resolve Figma's per-arch ZIP URLs from Figma's release metadata."""

    name = "figma"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "mac-arm",
        "x86_64-darwin": "mac",
    }

    def _release_url(self, channel: str, local_version: str) -> str:
        return (
            f"https://desktop.figma.com/{channel}/RELEASE.json"
            f"?localVersion={quote(local_version, safe='')}"
        )

    async def _fetch_platform(
        self,
        session: aiohttp.ClientSession,
        channel: str,
        local_version: str,
    ) -> tuple[str, str]:
        url = self._release_url(channel, local_version)
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

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Fetch the latest Figma version and per-platform ZIP URLs."""
        # Figma stages release cohorts using localVersion. Asking as 0.0.0
        # returned 126.9.16 even while installed 126.10.11 remained supported.
        local_version = (
            context.current.version
            if context.current is not None and context.current.version is not None
            else "0.0.0"
        )
        versions: dict[str, str] = {}
        asset_urls: dict[str, str] = {}
        for platform, channel in self.PLATFORMS.items():
            version, url = await self._fetch_platform(session, channel, local_version)
            versions[platform] = version
            asset_urls[platform] = url
        return VersionInfo(
            version=verify_platform_versions(
                versions,
                context="Figma release metadata returned mismatched versions",
            ),
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
