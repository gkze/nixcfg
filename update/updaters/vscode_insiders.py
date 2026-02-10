"""Updaters for VS Code Insiders platform API metadata."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import aiohttp

    from libnix.models.sources import SourceEntry, SourceHashes

from update.net import fetch_json
from update.updaters.base import (
    ChecksumProvidedUpdater,
    VersionInfo,
    _verify_platform_versions,
)

VSCODE_PLATFORMS = {
    "aarch64-darwin": "darwin-arm64",
    "aarch64-linux": "linux-arm64",
    "x86_64-linux": "linux-x64",
}


class PlatformAPIUpdater(ChecksumProvidedUpdater):
    """Base updater for APIs that expose per-platform version/checksum fields."""

    VERSION_KEY: str = "version"
    CHECKSUM_KEY: str | None = None

    def _api_url(self, api_platform: str) -> str:
        raise NotImplementedError

    def _download_url(self, api_platform: str, info: VersionInfo) -> str:
        raise NotImplementedError

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch platform metadata and verify versions match across platforms."""

        async def _fetch_one(
            nix_plat: str,
            api_plat: str,
        ) -> tuple[str, dict[str, str]]:
            data = await fetch_json(
                session,
                self._api_url(api_plat),
                config=self.config,
            )
            return nix_plat, cast("dict[str, str]", data)

        results = await asyncio.gather(
            *(_fetch_one(p, k) for p, k in self.PLATFORMS.items()),
        )
        platform_info = dict(results)
        versions = {p: info[self.VERSION_KEY] for p, info in platform_info.items()}
        version = _verify_platform_versions(versions, self.name)
        return VersionInfo(version=version, metadata={"platform_info": platform_info})

    async def fetch_checksums(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> dict[str, str]:
        """Extract per-platform checksums from fetched metadata."""
        _ = session
        if not self.CHECKSUM_KEY:
            msg = "No CHECKSUM_KEY defined"
            raise NotImplementedError(msg)
        platform_info = info.metadata["platform_info"]
        return {p: platform_info[p][self.CHECKSUM_KEY] for p in self.PLATFORMS}

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build result with platform download URLs and computed hashes."""
        urls = {
            nix_plat: self._download_url(api_plat, info)
            for nix_plat, api_plat in self.PLATFORMS.items()
        }
        return self._build_result_with_urls(info, hashes, urls)


class VSCodeInsidersUpdater(PlatformAPIUpdater):
    """Updater for upstream VS Code Insiders builds."""

    name = "vscode-insiders"
    PLATFORMS = VSCODE_PLATFORMS
    VERSION_KEY = "productVersion"
    CHECKSUM_KEY = "sha256hash"

    def _api_url(self, api_platform: str) -> str:
        return f"https://update.code.visualstudio.com/api/update/{api_platform}/insider/latest"

    def _download_url(self, api_platform: str, info: VersionInfo) -> str:
        return f"https://update.code.visualstudio.com/{info.version}/{api_platform}/insider"
