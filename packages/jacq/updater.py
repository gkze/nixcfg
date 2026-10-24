"""Updater for Jacq macOS releases."""

import re
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlparse

from lib.update.net import request
from lib.update.updaters import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.core import UpdateContext

_LATEST_URL = "https://downloads.jacquard.dev/latest/mac-arm64.dmg"
_VERSION_PATTERN = re.compile(
    r"/(?:desktop/)?releases/(?P<version>[^/]+)/Jacq-(?P=version)-arm64\.dmg"
)


@register_updater
class JacqUpdater(DownloadUrlMetadataUpdater):
    """Resolve the latest Jacq release and hash its macOS DMG."""

    name = "jacq"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    URL_METADATA_CONTEXT = "Jacq metadata"

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve the current version from Jacq's official latest redirect."""
        _ = context
        resolved_url = (
            await request(session, _LATEST_URL, method="HEAD", config=self.config)
        ).url
        parsed_url = urlparse(resolved_url)
        match = _VERSION_PATTERN.fullmatch(parsed_url.path)
        if parsed_url.netloc != "downloads.jacquard.dev" or match is None:
            msg = f"Could not extract Jacq version from resolved URL: {resolved_url}"
            raise RuntimeError(msg)
        return VersionInfo(
            version=match.group("version"),
            metadata=DownloadUrlMetadata(url=resolved_url),
        )
