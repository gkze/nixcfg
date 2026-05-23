"""Updater for Arc macOS releases."""

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
class ArcUpdater(DownloadUrlMetadataUpdater):
    """Resolve Arc's versioned ZIP URL from its Sparkle appcast."""

    name = "arc"
    APPCAST_URL = "https://releases.arc.net/updates.xml"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
    URL_METADATA_CONTEXT = "Arc metadata"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Arc version and immutable download URL."""
        items = await fetch_sparkle_appcast_items(
            session,
            self.APPCAST_URL,
            config=self.config,
        )
        item = items[0]
        raw_version = require_version(
            item.short_version or item.version,
            context=self.APPCAST_URL,
        )
        version_match = re.match(r"([0-9]+(?:\.[0-9]+)+)", raw_version)
        if version_match is None:
            msg = f"Could not parse Arc version from {raw_version!r}"
            raise RuntimeError(msg)
        url = require_url(item.url, context=self.APPCAST_URL)
        return VersionInfo(
            version=version_match.group(1),
            metadata=DownloadUrlMetadata(url=url),
        )
