"""Updater for Warp Preview."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    import aiohttp


@register_updater
class WarpPreviewUpdater(DownloadHashUpdater):
    """Resolve Warp Preview from Warp's channel-version metadata."""

    name = "warp-preview"
    CHANNELS_URL = "https://releases.warp.dev/channel_versions.json"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Warp Preview channel version."""
        payload = await fetch_json(
            session,
            self.CHANNELS_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        data = json_utils.as_object_dict(payload, context=self.CHANNELS_URL)
        preview = json_utils.as_object_dict(
            data.get("preview"),
            context=f"{self.CHANNELS_URL} preview",
        )
        raw_version = json_utils.get_required_str(
            preview,
            "version",
            context=f"{self.CHANNELS_URL} preview",
        )
        version = raw_version.removeprefix("v")
        if not version:
            msg = f"Missing Warp Preview version in {self.CHANNELS_URL}"
            raise RuntimeError(msg)
        return VersionInfo(version=version)

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return Warp Preview's versioned DMG URL."""
        _ = platform
        return f"https://releases.warp.dev/preview/v{info.version}/WarpPreview.dmg"
