"""Updater for CleanShot X macOS DMG releases."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    import aiohttp


@register_updater
class CleanShotUpdater(DownloadHashUpdater):
    """Resolve CleanShot X from the vendor changelog and versioned DMG URL."""

    name = "cleanshot"
    CHANGELOG_URL = "https://cleanshot.com/changelog"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest CleanShot X version from the public changelog."""
        payload = await fetch_url(
            session,
            self.CHANGELOG_URL,
            user_agent=self.config.default_user_agent,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        match = re.search(
            rb'class="number"[^>]*>\s*([0-9]+(?:\.[0-9]+)+)\s*<',
            payload,
        )
        if match is None:
            msg = f"Could not find CleanShot version in {self.CHANGELOG_URL}"
            raise RuntimeError(msg)
        return VersionInfo(version=match.group(1).decode())

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return CleanShot X's versioned DMG URL."""
        _ = platform
        return f"https://updates.getcleanshot.com/v3/CleanShot-X-{info.version}.dmg"
