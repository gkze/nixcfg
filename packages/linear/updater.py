"""Updater for Linear desktop macOS releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata
from lib.update.updaters.vendor_feeds import fetch_electron_builder_artifact_url

if TYPE_CHECKING:
    import aiohttp


@register_updater
class LinearUpdater(DownloadUrlMetadataUpdater):
    """Resolve Linear's DMG from the upstream Electron update feed."""

    name = "linear"
    FEED_URL = "https://releases.linear.app/latest-mac.yml"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
    URL_METADATA_CONTEXT = "Linear metadata"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch Linear's latest macOS feed and return the DMG URL."""
        version, dmg_url = await fetch_electron_builder_artifact_url(
            session,
            self.FEED_URL,
            lambda url: url.endswith(".dmg"),
            config=self.config,
        )
        return VersionInfo(version=version, metadata=DownloadUrlMetadata(url=dmg_url))
