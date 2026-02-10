"""Updater for Cursor editor release metadata and downloads."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar, cast

if TYPE_CHECKING:
    import aiohttp

    from libnix.models.sources import SourceEntry, SourceHashes

from update.net import fetch_json
from update.updaters.base import (
    DownloadHashUpdater,
    VersionInfo,
    _verify_platform_versions,
)


class CodeCursorUpdater(DownloadHashUpdater):
    """Resolve Cursor versions and platform-specific download URLs."""

    name = "code-cursor"
    API_BASE = "https://www.cursor.com/api/download"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-x64",
        "aarch64-linux": "linux-arm64",
        "x86_64-linux": "linux-x64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch stable release metadata for all supported Cursor platforms."""

        async def _fetch_one(
            nix_plat: str,
            api_plat: str,
        ) -> tuple[str, dict[str, str]]:
            data = await fetch_json(
                session,
                f"{self.API_BASE}?platform={api_plat}&releaseTrack=stable",
                config=self.config,
            )
            return nix_plat, cast("dict[str, str]", data)

        results = await asyncio.gather(
            *(_fetch_one(p, k) for p, k in self.PLATFORMS.items()),
        )
        platform_info = dict(results)
        versions = {p: info["version"] for p, info in platform_info.items()}
        commits = {p: info["commitSha"] for p, info in platform_info.items()}
        version = _verify_platform_versions(versions, "Cursor")
        commit = _verify_platform_versions(commits, "Cursor commit")
        return VersionInfo(
            version=version,
            metadata={"commit": commit, "platform_info": platform_info},
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the download URL for a given nix platform."""
        return info.metadata["platform_info"][platform]["downloadUrl"]

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a source entry containing all Cursor platform artifacts."""
        urls = {p: self.get_download_url(p, info) for p in self.PLATFORMS}
        return self._build_result_with_urls(
            info,
            hashes,
            urls,
            commit=info.metadata["commit"],
        )
