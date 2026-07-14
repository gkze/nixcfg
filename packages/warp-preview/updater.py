"""Updater for Warp Preview."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import JsonFieldDownloadUpdater, register_updater


@register_updater
class WarpPreviewUpdater(JsonFieldDownloadUpdater):
    """Resolve Warp Preview versions from the channel versions JSON feed."""

    name = "warp-preview"
    JSON_URL = "https://releases.warp.dev/channel_versions.json"
    VERSION_PATH = ("preview", "version")
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "darwin"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://releases.warp.dev/preview/v{version}/WarpPreview.dmg"
    )

    def transform_version(self, raw: str) -> str:
        """Strip the ``v`` prefix from the feed's version field."""
        return raw.removeprefix("v")
