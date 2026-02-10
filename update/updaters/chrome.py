"""Updater for Google Chrome stable releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

if TYPE_CHECKING:
    import aiohttp

from update.net import fetch_json
from update.updaters.base import DownloadHashUpdater, VersionInfo


class GoogleChromeUpdater(DownloadHashUpdater):
    """Resolve latest Google Chrome version and download hashes."""

    name = "google-chrome"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
        "x86_64-linux": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest stable Chrome version from Chromium Dash."""
        data = cast(
            "list[dict[str, str]]",
            await fetch_json(
                session,
                "https://chromiumdash.appspot.com/fetch_releases?channel=Stable&platform=Mac&num=1",
                config=self.config,
            ),
        )
        if not data:
            msg = "No Chrome releases returned from chromiumdash"
            raise RuntimeError(msg)
        version = data[0].get("version")
        if not version:
            msg = f"Missing version in chromiumdash response: {data[0]}"
            raise RuntimeError(msg)
        return VersionInfo(version=version, metadata={})
