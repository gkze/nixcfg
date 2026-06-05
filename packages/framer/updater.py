"""Updater for Framer desktop macOS releases."""

from __future__ import annotations

from lib.update.updaters.base import version_endpoint_download_updater

FramerUpdater = version_endpoint_download_updater(
    "framer",
    version_url="https://updates.framer.com/electron/darwin/arm64/version-stable",
    platforms={
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    },
    download_url="https://updates.framer.com/electron/darwin/{platform_value}/Framer-{version}.zip",
    display_name="Framer",
    module=__name__,
)
