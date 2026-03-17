"""Updater for Commander DMG releases."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo

_CHANGELOG_VERSION_RE = re.compile(r"^##\s+([0-9]+(?:\.[0-9]+)+)\s+-\s+", re.MULTILINE)


class CommanderUpdater(DownloadHashUpdater):
    """Resolve Commander version from the public changelog."""

    name = "commander"
    CHANGELOG_URL = "https://thecommander.app/changelog.html"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://download.thecommander.app/release/Commander.dmg",
        "x86_64-darwin": "https://download.thecommander.app/release/Commander.dmg",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Parse the latest release version from the changelog page."""
        payload = await fetch_url(
            session,
            self.CHANGELOG_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
            user_agent=self.config.default_user_agent,
        )
        content = payload.decode(errors="replace")
        match = _CHANGELOG_VERSION_RE.search(content)
        if match is None:
            msg = "Could not parse latest Commander version from changelog"
            raise RuntimeError(msg)
        return VersionInfo(version=match.group(1), metadata={})
