"""Updater for Commander DMG releases."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import NO_METADATA

_MARKDOWN_CHANGELOG_VERSION_RE = re.compile(
    r"^##\s+([0-9]+(?:\.[0-9]+)+)\s+-\s+", re.MULTILINE
)
_HTML_CHANGELOG_VERSION_RE = re.compile(
    r"<h2\b[^>]*>\s*([0-9]+(?:\.[0-9]+)+)\s+-\s+[^<]+</h2>",
    re.IGNORECASE,
)


def _extract_latest_version(content: str) -> str | None:
    for pattern in (_MARKDOWN_CHANGELOG_VERSION_RE, _HTML_CHANGELOG_VERSION_RE):
        match = pattern.search(content)
        if match is not None:
            return match.group(1)
    return None


@register_updater
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
        version = _extract_latest_version(content)
        if version is None:
            msg = "Could not parse latest Commander version from changelog"
            raise RuntimeError(msg)
        return VersionInfo(version=version, metadata=NO_METADATA)
