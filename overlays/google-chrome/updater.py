"""Updater for Google Chrome stable releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

from lib.update.net import fetch_json
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import NO_METADATA


@register_updater
class GoogleChromeUpdater(DownloadHashUpdater):
    """Resolve latest Google Chrome version and download hashes."""

    name = "google-chrome"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
        "x86_64-linux": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest stable Chrome version from Chromium Dash."""
        payload = await fetch_json(
            session,
            "https://chromiumdash.appspot.com/fetch_releases?channel=Stable&platform=Mac&num=1",
            config=self.config,
        )
        if not isinstance(payload, list):
            msg = f"Unexpected chromiumdash payload type: {type(payload).__name__}"
            raise TypeError(msg)
        data = payload
        if not data:
            msg = "No Chrome releases returned from chromiumdash"
            raise RuntimeError(msg)
        release = data[0]
        if not isinstance(release, dict):
            msg = f"Unexpected chromiumdash release payload: {release!r}"
            raise TypeError(msg)
        version = release.get("version")
        if not isinstance(version, str) or not version:
            msg = f"Missing version in chromiumdash response: {data[0]}"
            raise RuntimeError(msg)
        return VersionInfo(version=version, metadata=NO_METADATA)
