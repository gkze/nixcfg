"""Updater for LM Studio."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    import aiohttp


@register_updater
class LmStudioUpdater(DownloadHashUpdater):
    """Resolve LM Studio from LM Studio's update metadata endpoint."""

    name = "lm-studio"
    UPDATE_URL = "https://versions-prod.lmstudio.ai/update/darwin/arm64/0.0.0"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest LM Studio version and build."""
        payload = await fetch_json(
            session,
            self.UPDATE_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        data = json_utils.as_object_dict(payload, context=self.UPDATE_URL)
        version = json_utils.get_required_str(data, "version", context=self.UPDATE_URL)
        build = json_utils.get_required_str(data, "build", context=self.UPDATE_URL)
        return VersionInfo(version=f"{version}-{build}")

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return LM Studio's versioned DMG URL for the platform."""
        arch = self.PLATFORMS[platform]
        return (
            f"https://installers.lmstudio.ai/darwin/{arch}/{info.version}/"
            f"LM-Studio-{info.version}-{arch}.dmg"
        )
