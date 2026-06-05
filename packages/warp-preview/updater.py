"""Updater for Warp Preview."""

from __future__ import annotations

from lib.update.updaters.base import json_field_download_updater

WarpPreviewUpdater = json_field_download_updater(
    "warp-preview",
    json_url="https://releases.warp.dev/channel_versions.json",
    version_path=("preview", "version"),
    platforms={"aarch64-darwin": "darwin"},
    download_url="https://releases.warp.dev/preview/v{version}/WarpPreview.dmg",
    display_name="Warp Preview",
    version_transform=lambda version: version.removeprefix("v"),
    module=__name__,
)
