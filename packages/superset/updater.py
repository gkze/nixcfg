"""Updater for Superset Desktop Linux AppImage metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

from lib.update.net import fetch_github_api
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import AssetURLsMetadata


@register_updater
class SupersetUpdater(DownloadHashUpdater):
    """Track Superset Desktop AppImage URL and hash for Linux."""

    name = "superset"
    GITHUB_OWNER = "superset-sh"
    GITHUB_REPO = "superset"
    _TAG_PREFIX = "desktop-v"

    _ASSET_ARCH: ClassVar[dict[str, str]] = {
        "x86_64-linux": "x86_64",
    }
    PLATFORMS: ClassVar[dict[str, str]] = dict.fromkeys(_ASSET_ARCH, "")

    @staticmethod
    def _asset_name(version: str, arch: str) -> str:
        return f"superset-{version}-{arch}.AppImage"

    @classmethod
    def _fallback_url(cls, version: str, arch: str) -> str:
        return (
            "https://github.com/superset-sh/superset/releases/download/"
            f"desktop-v{version}/{cls._asset_name(version, arch)}"
        )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve latest published desktop release and platform asset URLs."""
        payload = await fetch_github_api(
            session,
            config=self.config,
            api_path=f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest",
        )

        if not isinstance(payload, dict):
            msg = f"Unexpected release payload type: {type(payload).__name__}"
            raise TypeError(msg)

        tag_name = payload.get("tag_name")
        if not isinstance(tag_name, str) or not tag_name:
            msg = f"Missing tag_name in release payload: {payload!r}"
            raise RuntimeError(msg)
        if not tag_name.startswith(self._TAG_PREFIX):
            msg = f"Unexpected Superset release tag format: {tag_name}"
            raise RuntimeError(msg)

        version = tag_name.removeprefix(self._TAG_PREFIX)
        if not version:
            msg = f"Missing version segment in Superset release tag: {tag_name}"
            raise RuntimeError(msg)

        assets = payload.get("assets")
        if not isinstance(assets, list):
            msg = f"Missing assets in release payload for tag {tag_name}"
            raise TypeError(msg)

        asset_urls: dict[str, str] = {}
        for platform, arch in self._ASSET_ARCH.items():
            expected_name = self._asset_name(version, arch)
            download_url: str | None = None
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                if asset.get("name") != expected_name:
                    continue
                candidate = asset.get("browser_download_url")
                if isinstance(candidate, str) and candidate:
                    download_url = candidate
                    break
            if download_url is None:
                msg = (
                    "Could not find Superset desktop release asset "
                    f"{expected_name!r} in tag {tag_name}"
                )
                raise RuntimeError(msg)
            asset_urls[platform] = download_url

        return VersionInfo(version=version, metadata=AssetURLsMetadata(asset_urls))

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Construct the per-platform Superset AppImage download URL."""
        metadata = info.metadata
        asset_urls = (
            metadata.asset_urls if isinstance(metadata, AssetURLsMetadata) else None
        )
        if asset_urls is not None:
            candidate = asset_urls.get(platform)
            if isinstance(candidate, str) and candidate:
                return candidate

        arch = self._ASSET_ARCH.get(platform)
        if arch is None:
            msg = f"Unsupported platform for superset updater: {platform}"
            raise RuntimeError(msg)

        return self._fallback_url(info.version, arch)
