"""Updater for Sculptor release artifacts."""

from __future__ import annotations

from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

from lib.update.net import _request
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo


class SculptorUpdater(DownloadHashUpdater):
    """Resolve Sculptor version metadata from object-store headers."""

    name = "sculptor"
    BASE_URL = "https://imbue-sculptor-releases.s3.us-west-2.amazonaws.com/sculptor"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "Sculptor.dmg",
        "x86_64-darwin": "Sculptor-x86_64.dmg",
        "x86_64-linux": "AppImage/x64/Sculptor.AppImage",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Read the Last-Modified header and derive a date-based version."""
        url = f"{self.BASE_URL}/Sculptor.dmg"
        _payload, headers = await _request(
            session,
            url,
            method="HEAD",
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        last_modified = headers.get("Last-Modified", "")
        if not last_modified:
            msg = "No Last-Modified header from Sculptor download"
            raise RuntimeError(msg)
        try:
            dt = parsedate_to_datetime(last_modified)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            dt = dt.astimezone(UTC)
            version = dt.strftime("%Y-%m-%d")
        except TypeError, ValueError:
            version = last_modified[:10]
        return VersionInfo(version=version, metadata={})
