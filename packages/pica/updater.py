"""Updater for the pinned Pica macOS app archive."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import PinnedSourceDownloadUpdater, register_updater


@register_updater
class PicaUpdater(PinnedSourceDownloadUpdater):
    """Pinned download updater for the Pica macOS app archive."""

    name = "pica"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://f6n9fvfeuhzxxji6.public.blob.vercel-storage.com/Pica-{version}.dmg"
    )
