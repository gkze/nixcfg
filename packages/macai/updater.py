"""Updater for macai macOS releases."""

from __future__ import annotations

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
class MacAIUpdater(DownloadUrlMetadataUpdater):
    """Resolve macai's versioned universal ZIP URL from its appcast."""

    name = "macai"
    APPCAST_URL = "https://renset.dev/macai/appcast.xml"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
    URL_METADATA_CONTEXT = "macai metadata"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest macai version and immutable download URL."""
        items = await fetch_sparkle_appcast_items(
            session,
            self.APPCAST_URL,
            config=self.config,
        )
        item = items[0]
        version = require_version(
            item.short_version or item.version, context=self.APPCAST_URL
        )
        url = require_url(item.url, context=self.APPCAST_URL)
        return VersionInfo(version=version, metadata=DownloadUrlMetadata(url=url))
