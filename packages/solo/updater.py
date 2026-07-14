"""Updater for the pinned Solo macOS app archive."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import PinnedSourceDownloadUpdater, register_updater


@register_updater
class SoloUpdater(PinnedSourceDownloadUpdater):
    """Pinned download updater for the Solo macOS app archive."""

    name = "solo"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "universal"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://releases.soloterm.com/darwin/{platform_value}/"
        "Solo_{version}_{platform_value}.app.tar.gz"
    )
