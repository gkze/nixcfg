"""Updater for Framer desktop macOS releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    import aiohttp


@register_updater
class FramerUpdater(DownloadHashUpdater):
    """Resolve Framer's current version from Framer's Electron updater feed."""

    name = "framer"
    VERSION_URL = "https://updates.framer.com/electron/darwin/arm64/version-stable"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Framer version from the vendor version endpoint."""
        payload = await fetch_url(
            session,
            self.VERSION_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        version = payload.decode().strip()
        if not version:
            msg = f"Missing Framer version in {self.VERSION_URL}"
            raise RuntimeError(msg)
        return VersionInfo(version=version)

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return Framer's versioned ZIP URL for the platform."""
        arch = self.PLATFORMS[platform]
        return f"https://updates.framer.com/electron/darwin/{arch}/Framer-{info.version}.zip"
