"""Updater for the pinned Cogito macOS app archive."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import PinnedSourceDownloadUpdater, register_updater


@register_updater
class CogitoUpdater(PinnedSourceDownloadUpdater):
    """Pinned download updater for the Cogito macOS app archive."""

    name = "cogito"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://downloads.cogito.md/releases/{version}/1/Cogito-{version}-1-mac.zip"
    )
