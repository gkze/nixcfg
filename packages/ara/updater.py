"""Updater for the pinned Ara macOS app archive."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater

AraUpdater = pinned_source_download_updater(
    "ara",
    platforms={"aarch64-darwin": "arm64"},
    download_url="https://db.ara.so/storage/v1/object/public/releases/Ara_{version}.zip",
    module=__name__,
)
