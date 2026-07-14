"""Updater for the pinned Jacq macOS app archive."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import PinnedSourceDownloadUpdater, register_updater


@register_updater
class JacqUpdater(PinnedSourceDownloadUpdater):
    """Pinned download updater for the Jacq macOS app archive."""

    name = "jacq"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://downloads.jacquard.dev/releases/{version}/"
        "Jacq-darwin-arm64-{version}.zip"
    )
