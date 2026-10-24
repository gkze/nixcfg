"""Updater for Granola macOS app releases."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata
from lib.update.updaters.vendor_feeds import (
    fetch_electron_builder_manifest,
    require_url,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.core import UpdateContext


@register_updater
class GranolaUpdater(DownloadUrlMetadataUpdater):
    """Resolve Granola versions from the Electron updater feed."""

    name = "granola"
    FEED_URL = "https://api.granola.ai/v1/check-for-update/latest-mac.yml"
    DOWNLOAD_BASE_URL = "https://dr2v7l5emb758.cloudfront.net"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
    URL_METADATA_CONTEXT = "Granola metadata"

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Pin the versioned universal zip named by Granola's Electron feed."""
        _ = context
        feed = await fetch_electron_builder_manifest(
            session, self.FEED_URL, config=self.config
        )
        path = require_url(feed.path, context=self.FEED_URL)
        return VersionInfo(
            version=feed.version,
            metadata=DownloadUrlMetadata(
                url=f"{self.DOWNLOAD_BASE_URL}/{feed.version}/{path}"
            ),
        )
