"""Updater for Factory Droid CLI releases."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes

from lib.update.net import fetch_url
from lib.update.updaters.base import ChecksumProvidedUpdater, VersionInfo


class DroidUpdater(ChecksumProvidedUpdater):
    """Resolve Droid version and per-platform checksums from release assets."""

    name = "droid"

    INSTALL_SCRIPT_URL = "https://app.factory.ai/cli"
    BASE_URL = "https://downloads.factory.ai/factory-cli/releases"

    _PLATFORM_INFO: ClassVar[dict[str, tuple[str, str]]] = {
        "aarch64-darwin": ("darwin", "arm64"),
        "x86_64-darwin": ("darwin", "x64"),
        "aarch64-linux": ("linux", "arm64"),
        "x86_64-linux": ("linux", "x64"),
    }
    PLATFORMS: ClassVar[dict[str, str]] = dict.fromkeys(_PLATFORM_INFO, "")

    def _download_url(self, nix_platform: str, version: str) -> str:
        os_name, arch = self._PLATFORM_INFO[nix_platform]
        return f"{self.BASE_URL}/{version}/{os_name}/{arch}/droid"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Parse the latest version from the install bootstrap script."""
        script = await fetch_url(
            session,
            self.INSTALL_SCRIPT_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        match = re.search(r'VER="([^"]+)"', script.decode())
        if not match:
            msg = "Could not parse version from Factory CLI install script"
            raise RuntimeError(msg)
        return VersionInfo(version=match.group(1), metadata={})

    async def fetch_checksums(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> dict[str, str]:
        """Fetch checksum sidecar files for all supported platforms."""
        checksum_urls = {
            nix_platform: f"{self._download_url(nix_platform, info.version)}.sha256"
            for nix_platform in self._PLATFORM_INFO
        }
        return await self._fetch_checksums_from_urls(session, checksum_urls)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a source entry keyed by Droid platform download URLs."""
        urls = {p: self._download_url(p, info.version) for p in self._PLATFORM_INFO}
        return self._build_result_with_urls(info, hashes, urls)
