"""Updater for the pinned Ara macOS app archive."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import PinnedSourceDownloadUpdater, register_updater


@register_updater
class AraUpdater(PinnedSourceDownloadUpdater):
    """Pinned download updater for the Ara macOS app archive."""

    name = "ara"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://db.ara.so/storage/v1/object/public/releases/Ara_{version}.zip"
    )
