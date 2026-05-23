"""Updater for Tailscale app."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.vendor_feeds import (
    fetch_sparkle_appcast_items,
    require_version,
)

if TYPE_CHECKING:
    import aiohttp


@register_updater
class TailscaleAppUpdater(DownloadHashUpdater):
    """Resolve Tailscale's GUI app version from the upstream stable appcast."""

    name = "tailscale-app"
    APPCAST_URL = "https://pkgs.tailscale.com/stable/appcast.xml"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Tailscale GUI app version."""
        items = await fetch_sparkle_appcast_items(
            session,
            self.APPCAST_URL,
            config=self.config,
        )
        item = items[0]
        version = require_version(
            item.short_version or item.version,
            context=self.APPCAST_URL,
        )
        return VersionInfo(version=version)

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return Tailscale's versioned macOS package URL."""
        _ = platform
        return f"https://pkgs.tailscale.com/stable/Tailscale-{info.version}-macos.pkg"
