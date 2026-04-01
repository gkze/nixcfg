"""Updater for Granola macOS app releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

import yaml

if TYPE_CHECKING:
    import aiohttp

from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater


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

    @staticmethod
    def _require_str(payload: dict[str, object], field: str) -> str:
        value = payload.get(field)
        if isinstance(value, str):
            return value
        msg = f"Expected string field {field!r} in Granola feed"
        raise RuntimeError(msg)

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the versioned universal zip URL for ``platform``."""
        _ = platform
        metadata = cast("dict[str, object]", info.metadata)
        path = metadata.get("path")
        if not isinstance(path, str):
            msg = "Expected string path metadata for Granola"
            raise TypeError(msg)
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
        if not isinstance(loaded, dict):
            msg = "Expected mapping payload from Granola updater feed"
            raise TypeError(msg)

        data = cast("dict[str, object]", loaded)
        version = self._require_str(data, "version")
        path = self._require_str(data, "path")
        sha512 = self._require_str(data, "sha512")
        return VersionInfo(version=version, metadata={"path": path, "sha512": sha512})
