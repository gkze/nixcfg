"""Updater for Spotify."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.vendor_feeds import fetch_head_artifact_version

if TYPE_CHECKING:
    import aiohttp


@register_updater
class SpotifyUpdater(DownloadHashUpdater):
    """Track Spotify's stable ARM installer URL by vendor HTTP metadata."""

    name = "spotify"
    DOWNLOAD_URL = "https://download.scdn.co/SpotifyARM64.dmg"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": DOWNLOAD_URL,
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch a version token from Spotify's mutable installer headers."""
        version = await fetch_head_artifact_version(
            session,
            self.DOWNLOAD_URL,
            config=self.config,
        )
        return VersionInfo(version=version)
