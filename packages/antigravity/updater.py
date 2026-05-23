"""Updater for Google Antigravity."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters.base import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata

if TYPE_CHECKING:
    import aiohttp


@register_updater
class AntigravityUpdater(DownloadUrlMetadataUpdater):
    """Resolve Google Antigravity from Google's updater service."""

    name = "antigravity"
    UPDATE_URL = (
        "https://antigravity-auto-updater-974169037036.us-central1.run.app/"
        "api/update/darwin-arm64/stable/latest"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm",
    }
    URL_METADATA_CONTEXT = "Google Antigravity metadata"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Antigravity version and immutable DMG URL."""
        payload = await fetch_json(
            session,
            self.UPDATE_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        data = json_utils.as_object_dict(payload, context=self.UPDATE_URL)
        zip_url = json_utils.get_required_str(data, "url", context=self.UPDATE_URL)
        match = re.search(r"/antigravity-hub/([^/]+)/", zip_url)
        if match is None:
            msg = f"Could not parse Antigravity version from URL: {zip_url}"
            raise RuntimeError(msg)
        url = zip_url.removesuffix(".zip") + ".dmg"
        return VersionInfo(
            version=match.group(1),
            metadata=DownloadUrlMetadata(url=url),
        )
