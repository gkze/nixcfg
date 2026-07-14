"""Updater for Framer desktop macOS releases."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import VersionEndpointDownloadUpdater, register_updater


@register_updater
class FramerUpdater(VersionEndpointDownloadUpdater):
    """Resolve Framer versions from its Electron stable version endpoint."""

    name = "framer"
    VERSION_URL = "https://updates.framer.com/electron/darwin/arm64/version-stable"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://updates.framer.com/electron/darwin/{platform_value}/"
        "Framer-{version}.zip"
    )
