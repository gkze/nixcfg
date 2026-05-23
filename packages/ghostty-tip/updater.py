"""Updater for Ghostty tip."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters.base import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata
from lib.update.updaters.vendor_feeds import (
    fetch_sparkle_appcast_items,
    require_url,
    require_version,
)

if TYPE_CHECKING:
    import aiohttp


@register_updater
class GhosttyTipUpdater(DownloadUrlMetadataUpdater):
    """Resolve Ghostty's nightly tip build from the upstream appcast."""

    name = "ghostty-tip"
    APPCAST_URL = "https://tip.files.ghostty.org/appcast.xml"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
    }
    URL_METADATA_CONTEXT = "Ghostty tip metadata"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Ghostty tip build and immutable DMG URL."""
        items = await fetch_sparkle_appcast_items(
            session,
            self.APPCAST_URL,
            config=self.config,
        )
        item = items[0]
        build = require_version(item.version, context=self.APPCAST_URL)
        url = require_url(item.url, context=self.APPCAST_URL)
        match = re.search(r"/([0-9a-f]{40})/Ghostty\.dmg$", url)
        if match is None:
            msg = f"Could not parse Ghostty tip commit from URL: {url}"
            raise RuntimeError(msg)
        return VersionInfo(
            version=f"{build}-{match.group(1)}",
            metadata=DownloadUrlMetadata(url=url),
        )
