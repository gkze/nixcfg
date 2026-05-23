"""Updater for Airfoil macOS releases."""

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
class AirfoilUpdater(DownloadHashUpdater):
    """Resolve Airfoil's current Sparkle version and hash the stable ZIP."""

    name = "airfoil"
    APPCAST_URL = (
        "https://rogueamoeba.net/ping/versionCheck.cgi?"
        "format=sparkle&system=999&bundleid=com.rogueamoeba.airfoil"
        "&platform=osx&version=51268000"
    )
    DOWNLOAD_URL = "https://cdn.rogueamoeba.com/airfoil/mac/download/Airfoil.zip"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": DOWNLOAD_URL,
        "x86_64-darwin": DOWNLOAD_URL,
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Airfoil version from Rogue Amoeba's Sparkle feed."""
        items = await fetch_sparkle_appcast_items(
            session,
            self.APPCAST_URL,
            config=self.config,
        )
        version = require_version(items[0].version, context=self.APPCAST_URL)
        return VersionInfo(version=version)
