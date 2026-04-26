"""Updater for Granola macOS app releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import yaml

if TYPE_CHECKING:
    import aiohttp

from lib import json_utils
from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import GranolaFeedMetadata, require_metadata_str


@register_updater
class GranolaUpdater(DownloadHashUpdater):
    """Resolve Granola versions from the Electron updater feed."""

    name = "granola"
    FEED_URL = "https://api.granola.ai/v1/check-for-update/latest-mac.yml"
    DOWNLOAD_BASE_URL = "https://dr2v7l5emb758.cloudfront.net"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the versioned universal zip URL for ``platform``."""
        _ = platform
        path = require_metadata_str(
            info.metadata,
            "path",
            context="Granola metadata",
        )
        return f"{self.DOWNLOAD_BASE_URL}/{info.version}/{path}"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Read version metadata from Granola's Electron updater feed."""
        payload = await fetch_url(
            session,
            self.FEED_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        loaded = yaml.safe_load(payload.decode())
        data = json_utils.coerce_json_object(
            loaded,
            context="Granola updater feed",
        )
        payload_map = json_utils.as_object_dict(data, context="Granola feed")
        version = json_utils.get_required_str(
            payload_map,
            "version",
            context="Granola feed",
        )
        path = json_utils.get_required_str(
            payload_map,
            "path",
            context="Granola feed",
        )
        sha512 = json_utils.get_required_str(
            payload_map,
            "sha512",
            context="Granola feed",
        )
        return VersionInfo(
            version=version,
            metadata=GranolaFeedMetadata(path=path, sha512=sha512),
        )
